import json
import os
import time

import httpx

from .config import RUNS_LOG, RESULTS_DIR


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
