"""
Orchestrator — LLM agent that dispatches browser sub-agents as tools.

The orchestrator thinks in a loop:
  decide → dispatch agent(s) / ask user / finish
Agent results and user replies feed back into the conversation.
"""
import asyncio
import json
import os
import re
from collections.abc import Callable, Awaitable

import httpx

from .config import NUM_AGENTS
from .pool import send_task as pool_send_task

MAX_STEPS = 30


# ---------------------------------------------------------------------------
# LLM call with retry (same resilience as browser agents)
# ---------------------------------------------------------------------------

async def _llm(prompt: str, model: str, api_key: str) -> str:
	key = api_key.strip() or os.getenv('GOOGLE_API_KEY', '')
	for attempt in range(50):
		try:
			async with httpx.AsyncClient(timeout=120) as client:
				resp = await client.post(
					f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
					params={'key': key},
					json={'contents': [{'parts': [{'text': prompt}]}]},
				)
				resp.raise_for_status()
			return resp.json()['candidates'][0]['content']['parts'][0]['text']
		except Exception:
			if attempt < 49:
				await asyncio.sleep(2)
				continue
			raise


def _extract_json(raw: str):
	for pattern in (r'\{[\s\S]*\}', r'\[[\s\S]*\]'):
		m = re.search(pattern, raw)
		if m:
			try:
				return json.loads(m.group())
			except json.JSONDecodeError:
				pass
	return None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM = f"""You are an AI orchestrator with access to {NUM_AGENTS} browser agents (slots 0–{NUM_AGENTS-1}).
Each agent controls a real Chrome browser and can navigate, click, type, and extract information.

YOUR ENTIRE RESPONSE MUST BE A SINGLE JSON OBJECT. No prose. No explanation. No markdown. ONLY JSON.

Available actions:

1. Call a single agent:
   {{"action": "call_agent", "slot_id": 0, "task": "complete self-contained instruction for the browser agent"}}

2. Call multiple agents in parallel (when tasks are independent):
   {{"action": "call_agents_parallel", "tasks": [{{"slot_id": 0, "task": "..."}}, {{"slot_id": 1, "task": "..."}}]}}

3. Ask the user a question (use only when you genuinely need input to proceed):
   {{"action": "ask_user", "question": "your question here"}}

4. Finish when you have a complete answer:
   {{"action": "finish", "answer": "comprehensive final answer based on what agents reported"}}

Rules:
- slot_id must be 0, 1, or 2
- DO NOT call "finish" as your first action — dispatch at least one agent first
- DO NOT describe what you plan to do — just output the JSON action immediately
- If an agent reports errors or partial completion, decide whether to retry, ask user, or accept
"""


def _build_system(skills: dict[str, str] | None, context: str) -> str:
	parts = [_SYSTEM]
	if context.strip():
		parts.append(f'USER CONTEXT (use this to fill in usernames, URLs, etc.):\n{context.strip()}')
	if skills:
		skill_lines = '\n'.join(f'  - "{name}": {prompt[:120]}…' for name, prompt in skills.items())
		parts.append(
			f'AVAILABLE SKILLS (pre-written task prompts you can pass verbatim to agents):\n{skill_lines}\n'
			'When a skill matches the goal, use its full prompt text as the agent task.'
		)
	return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Decision step
# ---------------------------------------------------------------------------

async def _decide(
	goal: str,
	history: list[dict],
	model: str,
	api_key: str,
	skills: dict[str, str] | None = None,
	context: str = '',
) -> dict:
	system = _build_system(skills, context)
	conv = '\n'.join(f"[{m['role']}] {m['content']}" for m in history)
	prompt = (
		f'{system}\n\nGOAL: {goal}\n\n'
		f'{"CONVERSATION:\n" + conv if conv else "(first step — dispatch an agent now)"}'
		f'\n\nYour next action (JSON only):'
	)
	raw = await _llm(prompt, model, api_key)
	parsed = _extract_json(raw)
	if isinstance(parsed, dict) and 'action' in parsed:
		return parsed

	retry_prompt = (
		f'{prompt}\n\nWARNING: Your last response was not valid JSON. '
		'Respond with ONLY a JSON object. Example:\n'
		'{"action": "call_agent", "slot_id": 0, "task": "..."}'
	)
	raw2 = await _llm(retry_prompt, model, api_key)
	parsed2 = _extract_json(raw2)
	if isinstance(parsed2, dict) and 'action' in parsed2:
		return parsed2

	if not history:
		return {'action': 'call_agent', 'slot_id': 0, 'task': goal}
	return {'action': 'finish', 'answer': raw}


# ---------------------------------------------------------------------------
# Run a browser sub-agent
# ---------------------------------------------------------------------------

async def _run_agent(slot_id: int, task: str, api_key: str, model: str) -> str:
	history, _ = await pool_send_task(slot_id, task, api_key, model, [])
	if history and history[-1]['role'] == 'assistant':
		return history[-1]['content']
	return 'No result returned.'


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def orchestrate(
	task: str,
	api_key: str,
	model: str,
	on_thought: Callable[[str], None],
	on_agent_start: Callable[[int, str], None],
	on_agent_result: Callable[[int, str, str], None],
	on_finish: Callable[[str], None],
	on_ask_user: Callable[[str], Awaitable[str]] | None = None,
	get_injection: Callable[[], str | None] | None = None,
	skills: dict[str, str] | None = None,
	context: str = '',
) -> str:
	history: list[dict] = []

	for step in range(1, MAX_STEPS + 1):
		# Drain any pending user injection before deciding
		if get_injection:
			injection = get_injection()
			if injection:
				history.append({'role': 'user', 'content': injection})

		decision = await _decide(task, history, model, api_key, skills=skills, context=context)
		action = decision.get('action', 'finish')

		# ── finish ────────────────────────────────────────────────────────
		if action == 'finish':
			answer = decision.get('answer', 'Done.')
			on_finish(answer)
			await asyncio.sleep(0)
			return answer

		# ── ask_user ──────────────────────────────────────────────────────
		elif action == 'ask_user':
			question = decision.get('question', '')
			history.append({'role': 'orchestrator', 'content': f'[Asked user] {question}'})
			if on_ask_user:
				on_thought(f'Waiting for your input…')
				await asyncio.sleep(0)
				user_reply = await on_ask_user(question)
				history.append({'role': 'user', 'content': user_reply})
			else:
				history.append({'role': 'user', 'content': '(no user handler — continue)'})

		# ── call_agent ────────────────────────────────────────────────────
		elif action == 'call_agent':
			slot_id = int(decision.get('slot_id', 0)) % NUM_AGENTS
			subtask = decision.get('task', task)
			on_thought(f'Step {step}: dispatching Agent {slot_id+1}')
			on_agent_start(slot_id, subtask)
			await asyncio.sleep(0)
			history.append({'role': 'orchestrator', 'content': f'→ Agent {slot_id+1}: {subtask}'})

			result = await _run_agent(slot_id, subtask, api_key, model)
			on_agent_result(slot_id, subtask, result)
			await asyncio.sleep(0)
			history.append({'role': f'agent_{slot_id+1}', 'content': result})

		# ── call_agents_parallel ──────────────────────────────────────────
		elif action == 'call_agents_parallel':
			raw_tasks = decision.get('tasks', [])
			if not raw_tasks:
				continue
			on_thought(f'Step {step}: dispatching {len(raw_tasks)} agents in parallel')
			for t in raw_tasks:
				s = int(t.get('slot_id', 0)) % NUM_AGENTS
				on_agent_start(s, t.get('task', ''))
				history.append({'role': 'orchestrator', 'content': f'→ Agent {s+1}: {t.get("task","")}'})
			await asyncio.sleep(0)

			async def _one(t=None):
				s = int(t.get('slot_id', 0)) % NUM_AGENTS
				r = await _run_agent(s, t.get('task', ''), api_key, model)
				on_agent_result(s, t.get('task', ''), r)
				await asyncio.sleep(0)
				return s, r

			pairs = await asyncio.gather(*[_one(t) for t in raw_tasks])
			for s, r in pairs:
				history.append({'role': f'agent_{s+1}', 'content': r})

		else:
			on_finish(f'Unknown action "{action}" — stopping.')
			return f'Stopped at step {step}.'

	answer = 'Reached maximum steps.'
	on_finish(answer)
	return answer
