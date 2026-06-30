import json
import os
import time

import httpx

from .config import RUNS_LOG, RESULTS_DIR, LEARNED_LOG


def log_run(slot_id: int, task: str, result: str) -> None:
	ts = time.strftime('%Y-%m-%dT%H:%M:%S')

	# Append to JSON run log
	runs = []
	if RUNS_LOG.exists():
		runs = json.loads(RUNS_LOG.read_text())
	runs.append({'slot': slot_id + 1, 'task': task, 'result': result, 'ts': ts})
	RUNS_LOG.write_text(json.dumps(runs[-50:], indent=2))

	# Write individual markdown result file
	RESULTS_DIR.mkdir(exist_ok=True)
	safe_task = ''.join(c if c.isalnum() or c in ' -_' else '' for c in task)[:60].strip()
	filename = f"{ts.replace(':', '-')}_agent{slot_id+1}_{safe_task}.md"
	md = f'# Agent {slot_id+1} Result\n\n**Task:** {task}\n\n**Time:** {ts}\n\n---\n\n{result}\n'
	(RESULTS_DIR / filename).write_text(md, encoding='utf-8')


async def reflect_on_runs(api_key: str, model: str) -> str:
	if not RUNS_LOG.exists():
		return 'No run history yet.'
	key = api_key.strip() or os.getenv('GOOGLE_API_KEY', '')
	if not key:
		return 'Error: No Google API key.'
	runs = json.loads(RUNS_LOG.read_text())[-10:]
	summary = '\n'.join(
		f"Agent {r['slot']} | Task: {r['task']}\nResult: {r['result'][:200]}" for r in runs
	)
	prompt = (
		'Analyze these recent browser agent runs and suggest concrete improvements '
		f'to prompts or strategies:\n\n{summary}'
	)
	async with httpx.AsyncClient(timeout=30) as client:
		resp = await client.post(
			f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
			params={'key': key},
			json={'contents': [{'parts': [{'text': prompt}]}]},
		)
		resp.raise_for_status()
		data = resp.json()
	return data['candidates'][0]['content']['parts'][0]['text']


def load_learned() -> str:
	"""Return the persisted self-improved operating instructions (empty if none)."""
	if not LEARNED_LOG.exists():
		return ''
	try:
		data = json.loads(LEARNED_LOG.read_text())
	except (json.JSONDecodeError, OSError):
		return ''
	return (data.get('instructions') or '').strip()


async def distill_lessons(api_key: str, model: str) -> str:
	"""Fold recent runs into durable standing instructions and persist them.

	This is the write-back counterpart to ``reflect_on_runs``: that one only
	*shows* advice, this one merges the existing learned instructions with
	transferable lessons from recent runs (deduplicated) and saves the result,
	so future agents pick it up via ``extend_system_message``. Returns the saved
	instruction text (unchanged on error so a flaky LLM call can't wipe it).
	"""
	if not RUNS_LOG.exists():
		return load_learned()
	key = api_key.strip() or os.getenv('GOOGLE_API_KEY', '')
	if not key:
		return load_learned()
	runs = json.loads(RUNS_LOG.read_text())[-10:]
	if not runs:
		return load_learned()
	existing = load_learned()
	summary = '\n'.join(
		f"Agent {r['slot']} | Task: {r['task']}\nResult: {r['result'][:300]}" for r in runs
	)
	prompt = (
		'You are improving the standing instructions for a browser-automation agent.\n'
		'Below are the current learned instructions (may be empty) and a log of recent runs.\n'
		'Return an updated, deduplicated bullet list of DURABLE, TRANSFERABLE operating '
		'instructions that would make future runs more reliable: general tactics, recurring '
		'failure modes to avoid, site-agnostic heuristics. Do NOT include task-specific facts '
		'or one-off details. Keep it under 12 concise bullets. Output only the bullet list.\n\n'
		f'CURRENT INSTRUCTIONS:\n{existing or "(none yet)"}\n\n'
		f'RECENT RUNS:\n{summary}'
	)
	try:
		async with httpx.AsyncClient(timeout=30) as client:
			resp = await client.post(
				f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
				params={'key': key},
				json={'contents': [{'parts': [{'text': prompt}]}]},
			)
			resp.raise_for_status()
			data = resp.json()
		instructions = data['candidates'][0]['content']['parts'][0]['text'].strip()
	except Exception:  # noqa: BLE001 — keep prior learnings if the LLM call fails
		return existing
	if not instructions:
		return existing
	LEARNED_LOG.write_text(
		json.dumps({'instructions': instructions, 'ts': time.strftime('%Y-%m-%dT%H:%M:%S')}, indent=2)
	)
	return instructions
