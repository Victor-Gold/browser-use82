"""
Browser Use UI — multi-agent edition.
One Chrome, N concurrent agents each in their own tab.
"""
import asyncio
import glob
import json
import os
import subprocess
import time
from functools import partial
from pathlib import Path

import httpx
from dotenv import load_dotenv
import gradio as gr

from browser_use import Agent, Browser, ChatGoogle

load_dotenv()

CHROME_DEBUG_PORT = 9222
CHROME_USER_DATA = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'browseruse-chrome-profile')
SKILLS_FILE = Path(__file__).parent / 'skills.json'
RUNS_LOG = Path(__file__).parent / 'runs_log.json'
NUM_AGENTS = 3  # number of parallel agent slots

_chrome_proc: subprocess.Popen | None = None
_shared_browser: Browser | None = None

# Per-agent state keyed by slot index 0..NUM_AGENTS-1
_agents: dict[int, Agent | None] = {i: None for i in range(NUM_AGENTS)}
_agent_tasks: dict[int, asyncio.Task | None] = {i: None for i in range(NUM_AGENTS)}


# ---------------------------------------------------------------------------
# Chrome helpers
# ---------------------------------------------------------------------------

def _find_chrome() -> str:
	localappdata = os.environ.get('LOCALAPPDATA', '')
	patterns = [
		os.path.join(localappdata, r'ms-playwright\chromium-*\chrome-win64\chrome.exe'),
		os.path.join(localappdata, r'ms-playwright\chromium-*\chrome-win\chrome.exe'),
		r'C:\Program Files\Google\Chrome\Application\chrome.exe',
		r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
		os.path.join(localappdata, r'Google\Chrome\Application\chrome.exe'),
	]
	for pattern in patterns:
		matches = glob.glob(pattern)
		if matches:
			return sorted(matches)[-1]
		if os.path.isfile(pattern):
			return pattern
	raise RuntimeError('No Chrome/Chromium found.')


def _chrome_is_ready() -> bool:
	try:
		r = httpx.get(f'http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version', timeout=1.0)
		return r.status_code == 200
	except Exception:
		return False


def launch_chrome() -> str:
	global _chrome_proc, _shared_browser
	if _chrome_is_ready():
		return f'Chrome already running on port {CHROME_DEBUG_PORT}'
	chrome = _find_chrome()
	os.makedirs(CHROME_USER_DATA, exist_ok=True)
	_chrome_proc = subprocess.Popen(
		[chrome, f'--remote-debugging-port={CHROME_DEBUG_PORT}',
		 f'--user-data-dir={CHROME_USER_DATA}',
		 '--no-first-run', '--no-default-browser-check'],
		stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
	)
	for _ in range(15):
		time.sleep(1)
		if _chrome_is_ready():
			_shared_browser = Browser(cdp_url=f'http://localhost:{CHROME_DEBUG_PORT}', keep_alive=True)
			return f'Chrome launched (pid {_chrome_proc.pid}) — {NUM_AGENTS} agent slots ready'
	return 'Error: Chrome did not respond in time'


def kill_chrome() -> str:
	global _chrome_proc, _shared_browser
	for i in range(NUM_AGENTS):
		if _agents[i] and _agent_tasks[i] and not _agent_tasks[i].done():
			_agents[i].stop()
		_agents[i] = None
		_agent_tasks[i] = None
	_shared_browser = None
	if _chrome_proc and _chrome_proc.poll() is None:
		_chrome_proc.terminate()
		_chrome_proc = None
		return 'Chrome stopped'
	_chrome_proc = None
	return 'Chrome was not running'


# ---------------------------------------------------------------------------
# Per-agent controls (slot_id selects which agent)
# ---------------------------------------------------------------------------

def pause_agent(slot_id: int) -> str:
	agent = _agents[slot_id]
	task = _agent_tasks[slot_id]
	if agent and task and not task.done():
		agent.pause()
		return f'Agent {slot_id+1} paused'
	return f'Agent {slot_id+1} not running'


def stop_agent(slot_id: int) -> str:
	agent = _agents[slot_id]
	task = _agent_tasks[slot_id]
	if agent and task and not task.done():
		agent.stop()
		return f'Agent {slot_id+1} stopped'
	return f'Agent {slot_id+1} not running'


async def resume_agent(slot_id: int, instruction: str) -> str:
	agent = _agents[slot_id]
	if agent is None:
		return f'Agent {slot_id+1} not initialized'
	if instruction.strip():
		agent.add_new_task(instruction)
	agent.resume()
	return f'Agent {slot_id+1} resumed'


def reset_agent(slot_id: int) -> tuple[str, list]:
	agent = _agents[slot_id]
	task = _agent_tasks[slot_id]
	if agent and task and not task.done():
		agent.stop()
	_agents[slot_id] = None
	_agent_tasks[slot_id] = None
	return f'Agent {slot_id+1} reset', []


# ---------------------------------------------------------------------------
# Skills library
# ---------------------------------------------------------------------------

def _load_skills() -> dict[str, str]:
	if SKILLS_FILE.exists():
		return json.loads(SKILLS_FILE.read_text())
	return {}


def load_skill(name: str) -> str:
	return _load_skills().get(name, '')


def save_skill(name: str, prompt: str) -> str:
	if not name.strip() or not prompt.strip():
		return 'Provide both a name and a prompt'
	skills = _load_skills()
	skills[name.strip()] = prompt.strip()
	SKILLS_FILE.write_text(json.dumps(skills, indent=2))
	return f'Saved "{name}"'


# ---------------------------------------------------------------------------
# Run log + reflection
# ---------------------------------------------------------------------------

def _log_run(slot_id: int, task: str, result: str) -> None:
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
	prompt = f'Analyze these recent browser agent runs and suggest concrete improvements to prompts or strategies:\n\n{summary}'
	async with httpx.AsyncClient(timeout=30) as client:
		resp = await client.post(
			f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
			params={'key': key},
			json={'contents': [{'parts': [{'text': prompt}]}]},
		)
		resp.raise_for_status()
		data = resp.json()
	return data['candidates'][0]['content']['parts'][0]['text']


# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------

async def _run_agent(slot_id: int) -> tuple[str, int]:
	agent_history = await _agents[slot_id].run()
	result = agent_history.final_result() or 'Done — check the browser.'
	steps = getattr(agent_history, 'number_of_steps', lambda: 0)()
	return result, steps


async def send_task(slot_id: int, task: str, api_key: str, model: str, history: list) -> tuple[list, str]:
	def msg(role: str, content: str) -> dict:
		return {'role': role, 'content': content}

	if not task.strip():
		return history, ''
	if not api_key.strip() and not os.getenv('GOOGLE_API_KEY'):
		return history + [msg('user', task), msg('assistant', 'Error: No Google API key.')], ''
	if api_key.strip():
		os.environ['GOOGLE_API_KEY'] = api_key
	if not _chrome_is_ready():
		return history + [msg('user', task), msg('assistant', 'Error: Launch Chrome first.')], ''

	try:
		llm = ChatGoogle(model=model)

		if _agents[slot_id] is None:
			browser = Browser(cdp_url=f'http://localhost:{CHROME_DEBUG_PORT}', keep_alive=True)
			_agents[slot_id] = Agent(task=task, llm=llm, browser=browser, max_failures=50)
		else:
			_agents[slot_id].llm = llm
			_agents[slot_id].add_new_task(task)

		_agent_tasks[slot_id] = asyncio.create_task(_run_agent(slot_id))
		result, _ = await _agent_tasks[slot_id]
		_log_run(slot_id, task, result)
		return history + [msg('user', task), msg('assistant', result)], ''

	except asyncio.CancelledError:
		_agents[slot_id] = None
		return history + [msg('user', task), msg('assistant', 'Stopped.')], ''
	except Exception as e:
		_agents[slot_id] = None
		return history + [msg('user', task), msg('assistant', f'Error: {e}')], ''


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def create_ui():
	skills = list(_load_skills().keys())

	with gr.Blocks(title='Browser Use — Multi-Agent') as interface:
		gr.Markdown('# Browser Use — Multi-Agent')

		# Global Chrome controls
		with gr.Row():
			launch_btn = gr.Button('Launch Chrome', variant='secondary')
			kill_btn = gr.Button('Stop Chrome', variant='stop')
			chrome_status = gr.Textbox(label='Chrome Status', interactive=False, scale=3)

		launch_btn.click(fn=launch_chrome, outputs=chrome_status)
		kill_btn.click(fn=kill_chrome, outputs=chrome_status)

		# Shared config + skills (outside tabs so it applies to all agents)
		with gr.Row():
			with gr.Column(scale=1):
				api_key = gr.Textbox(label='Google API Key (optional if in .env)', type='password')
				model = gr.Dropdown(
					choices=['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
					label='Model', value='gemma-4-31b-it',
				)
			with gr.Column(scale=1):
				skill_dropdown = gr.Dropdown(choices=skills, label='Load skill into Agent 1 task box')
				skill_name_box = gr.Textbox(label='Save current task as skill (name)')
				with gr.Row():
					load_skill_btn = gr.Button('Load')
					save_skill_btn = gr.Button('Save')
				skill_status = gr.Textbox(label='', interactive=False)
			with gr.Column(scale=1):
				reflect_btn = gr.Button('Reflect on recent runs')
				reflect_out = gr.Textbox(label='Self-improvement suggestions', lines=5, interactive=False)

		reflect_btn.click(fn=reflect_on_runs, inputs=[api_key, model], outputs=reflect_out)

		gr.Markdown('---')

		# One tab per agent slot
		agent_task_inputs = []  # for skill loading into agent 1

		with gr.Tabs():
			for slot_id in range(NUM_AGENTS):
				with gr.Tab(label=f'Agent {slot_id + 1}'):
					with gr.Row():
						p_btn = gr.Button('⏸ Pause', scale=1)
						s_btn = gr.Button('⏹ Stop', variant='stop', scale=1)
						r_btn = gr.Button('↺ Reset', variant='secondary', scale=1)
						slot_status = gr.Textbox(label='Status', interactive=False, scale=3)

					with gr.Row():
						inject_box = gr.Textbox(label='Inject instruction (while paused)', scale=4)
						resume_btn = gr.Button('▶ Resume', variant='primary', scale=1)

					chatbot = gr.Chatbot(label=f'Agent {slot_id + 1} conversation', height=350)

					with gr.Row():
						task_input = gr.Textbox(
							label='Task / Follow-up',
							placeholder=f'What should Agent {slot_id + 1} do?',
							scale=4,
						)
						send_btn = gr.Button('Send', variant='primary', scale=1)

					if slot_id == 0:
						agent_task_inputs.append(task_input)

					# Wire controls — use partial to capture slot_id
					p_btn.click(fn=partial(pause_agent, slot_id), outputs=slot_status)
					s_btn.click(fn=partial(stop_agent, slot_id), outputs=slot_status)
					r_btn.click(fn=partial(reset_agent, slot_id), outputs=[slot_status, chatbot])
					resume_btn.click(
						fn=partial(resume_agent, slot_id),
						inputs=inject_box, outputs=slot_status,
					)
					send_btn.click(
						fn=partial(send_task, slot_id),
						inputs=[task_input, api_key, model, chatbot],
						outputs=[chatbot, task_input],
					)
					task_input.submit(
						fn=partial(send_task, slot_id),
						inputs=[task_input, api_key, model, chatbot],
						outputs=[chatbot, task_input],
					)

		# Skill loading goes into Agent 1's task box
		if agent_task_inputs:
			load_skill_btn.click(fn=load_skill, inputs=skill_dropdown, outputs=agent_task_inputs[0])
			save_skill_btn.click(fn=save_skill, inputs=[skill_name_box, agent_task_inputs[0]], outputs=skill_status)

	return interface


if __name__ == '__main__':
	demo = create_ui()
	demo.launch(server_name='127.0.0.1', server_port=7860, share=False)
