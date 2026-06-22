"""
Orchestrator — an LLM agent that autonomously dispatches browser sub-agents as tools.

The orchestrator thinks in a loop:
  1. Given the goal + conversation so far, decide what to do next
  2. Options: call one agent, call multiple agents in parallel, or finish
  3. Agent results feed back into the conversation
  4. Repeat until the orchestrator calls "finish"

The browser agents are tools to the orchestrator, not pre-planned workers.
"""
import asyncio
import json
import os
import re
from collections.abc import Callable

import httpx

from .config import NUM_AGENTS
from .pool import send_task as pool_send_task

MAX_STEPS = 20  # hard cap on orchestrator reasoning steps


# ---------------------------------------------------------------------------
# Raw LLM call
# ---------------------------------------------------------------------------

async def _llm(prompt: str, model: str, api_key: str) -> str:
	key = api_key.strip() or os.getenv('GOOGLE_API_KEY', '')
	async with httpx.AsyncClient(timeout=120) as client:
		resp = await client.post(
			f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
			params={'key': key},
			json={'contents': [{'parts': [{'text': prompt}]}]},
		)
		resp.raise_for_status()
	return resp.json()['candidates'][0]['content']['parts'][0]['text']


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
# Orchestrator step — returns a structured action
# ---------------------------------------------------------------------------

_SYSTEM = f"""You are an AI orchestrator with access to {NUM_AGENTS} browser agents (slots 0–{NUM_AGENTS-1}).
Each agent controls a real Chrome browser and can navigate, click, type, and extract information.

At each step you MUST respond with exactly one JSON action. Choose from:

1. Call a single agent:
   {{"action": "call_agent", "slot_id": 0, "task": "precise instruction for the browser agent"}}

2. Call multiple agents in parallel (use when tasks are independent):
   {{"action": "call_agents_parallel", "tasks": [{{"slot_id": 0, "task": "..."}}, {{"slot_id": 1, "task": "..."}}]}}

3. Finish when you have enough information:
   {{"action": "finish", "answer": "comprehensive final answer"}}

Rules:
- slot_id must be 0, 1, or 2
- Don't repeat a task an agent already completed unless you need updated information
- Be specific in task instructions — agents work best with clear, self-contained instructions
- Return "finish" as soon as the goal is achievable from the information gathered
"""


async def _decide(goal: str, history: list[dict], model: str, api_key: str) -> dict:
	conv = '\n'.join(f"[{m['role']}] {m['content']}" for m in history)
	prompt = f'{_SYSTEM}\n\nGOAL: {goal}\n\n{"CONVERSATION:\n" + conv if conv else "(first step)"}\n\nYour next action (JSON only):'
	raw = await _llm(prompt, model, api_key)
	parsed = _extract_json(raw)
	if isinstance(parsed, dict) and 'action' in parsed:
		return parsed
	# If LLM didn't return valid JSON, treat raw text as a finish
	return {'action': 'finish', 'answer': raw}


# ---------------------------------------------------------------------------
# Run a browser sub-agent and return its result
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
	on_thought: Callable[[str], None],        # orchestrator decided to do X
	on_agent_start: Callable[[int, str], None],  # agent slot_id started with task
	on_agent_result: Callable[[int, str, str], None],  # slot_id, task, result
	on_finish: Callable[[str], None],
) -> str:
	"""
	Orchestrator loop. Runs until the LLM calls 'finish' or MAX_STEPS is hit.
	All callbacks are sync (UI updates) and fire from the async loop.
	"""
	history: list[dict] = []

	for step in range(1, MAX_STEPS + 1):
		decision = await _decide(task, history, model, api_key)
		action = decision.get('action', 'finish')

		# ── finish ────────────────────────────────────────────────────────
		if action == 'finish':
			answer = decision.get('answer', 'Done.')
			on_finish(answer)
			return answer

		# ── call_agent ────────────────────────────────────────────────────
		elif action == 'call_agent':
			slot_id = int(decision.get('slot_id', 0)) % NUM_AGENTS
			subtask = decision.get('task', task)
			on_thought(f'Step {step}: dispatching Agent {slot_id+1}')
			on_agent_start(slot_id, subtask)
			history.append({'role': 'orchestrator', 'content': f'→ Agent {slot_id+1}: {subtask}'})

			result = await _run_agent(slot_id, subtask, api_key, model)
			on_agent_result(slot_id, subtask, result)
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
				history.append({'role': 'orchestrator', 'content': f'→ Agent {s+1}: {t.get("task", "")}'})

			async def _one(t=None):
				s = int(t.get('slot_id', 0)) % NUM_AGENTS
				r = await _run_agent(s, t.get('task', ''), api_key, model)
				on_agent_result(s, t.get('task', ''), r)
				return s, r

			pairs = await asyncio.gather(*[_one(t) for t in raw_tasks])
			for s, r in pairs:
				history.append({'role': f'agent_{s+1}', 'content': r})

		else:
			# Unknown action — stop gracefully
			on_finish(f'Unknown action "{action}" — stopping.')
			return f'Stopped at step {step}.'

	answer = 'Reached maximum steps without finishing.'
	on_finish(answer)
	return answer
