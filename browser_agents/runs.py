import json
import os
import time

import httpx

from .config import RUNS_LOG


def log_run(slot_id: int, task: str, result: str) -> None:
	runs = []
	if RUNS_LOG.exists():
		runs = json.loads(RUNS_LOG.read_text())
	runs.append({'slot': slot_id + 1, 'task': task, 'result': result,
	             'ts': time.strftime('%Y-%m-%dT%H:%M:%S')})
	RUNS_LOG.write_text(json.dumps(runs[-50:], indent=2))


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
