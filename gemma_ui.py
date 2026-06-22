"""
Browser Use UI — Gemma edition.
Features:
  • Persistent Chrome session (launched once, reused)
  • Stateful agent with back-and-forth via add_new_task
  • Pause / Resume / Stop controls while agent is running
  • Mid-run instruction injection (pause → type → resume with new context)
  • Skill library (saved prompt templates in skills.json)
  • Run log for self-improvement reflection
"""
import asyncio
import glob
import json
import os
import subprocess
import time
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

_chrome_proc: subprocess.Popen | None = None
_agent: Agent | None = None
_agent_task: asyncio.Task | None = None


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
	global _chrome_proc
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
			return f'Chrome launched (pid {_chrome_proc.pid}) — ready'
	return 'Error: Chrome did not respond in time'


def kill_chrome() -> str:
	global _chrome_proc, _agent, _agent_task
	if _agent_task and not _agent_task.done():
		if _agent:
			_agent.stop()
	_agent = None
	_agent_task = None
	if _chrome_proc and _chrome_proc.poll() is None:
		_chrome_proc.terminate()
		_chrome_proc = None
		return 'Chrome stopped'
	_chrome_proc = None
	return 'Chrome was not running'


# ---------------------------------------------------------------------------
# Agent controls
# ---------------------------------------------------------------------------

def reset_agent() -> tuple[str, list]:
	global _agent, _agent_task
	if _agent_task and not _agent_task.done():
		if _agent:
			_agent.stop()
	_agent = None
	_agent_task = None
	return 'Agent reset — next task starts fresh', []


def pause_agent() -> str:
	if _agent and _agent_task and not _agent_task.done():
		_agent.pause()
		return 'Agent paused — type an instruction below then click Resume'
	return 'No agent running'


def stop_agent() -> str:
	global _agent, _agent_task
	if _agent and _agent_task and not _agent_task.done():
		_agent.stop()
		return 'Agent stopped'
	return 'No agent running'


async def resume_with_instruction(instruction: str) -> str:
	if _agent is None:
		return 'No agent to resume'
	if instruction.strip():
		_agent.add_new_task(instruction)
	_agent.resume()
	return 'Agent resumed'


# ---------------------------------------------------------------------------
# Skills library
# ---------------------------------------------------------------------------

def _load_skills() -> dict[str, str]:
	if SKILLS_FILE.exists():
		return json.loads(SKILLS_FILE.read_text())
	return {}


def _save_skills(skills: dict[str, str]) -> None:
	SKILLS_FILE.write_text(json.dumps(skills, indent=2))


def list_skill_names() -> list[str]:
	return list(_load_skills().keys())


def load_skill(name: str) -> str:
	return _load_skills().get(name, '')


def save_skill(name: str, prompt: str) -> str:
	if not name.strip() or not prompt.strip():
		return 'Provide both a name and a prompt to save'
	skills = _load_skills()
	skills[name.strip()] = prompt.strip()
	_save_skills(skills)
	return f'Saved skill "{name}"'


def delete_skill(name: str) -> tuple[str, list]:
	skills = _load_skills()
	if name in skills:
		del skills[name]
		_save_skills(skills)
		return f'Deleted skill "{name}"', list(skills.keys())
	return f'Skill "{name}" not found', list(skills.keys())


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def _log_run(task: str, result: str, steps: int) -> None:
	runs = []
	if RUNS_LOG.exists():
		runs = json.loads(RUNS_LOG.read_text())
	runs.append({'task': task, 'result': result, 'steps': steps,
	             'ts': time.strftime('%Y-%m-%dT%H:%M:%S')})
	runs = runs[-50:]  # keep last 50
	RUNS_LOG.write_text(json.dumps(runs, indent=2))


async def reflect_on_runs(api_key: str, model: str) -> str:
	if not RUNS_LOG.exists():
		return 'No run history yet.'
	if api_key.strip():
		os.environ['GOOGLE_API_KEY'] = api_key
	runs = json.loads(RUNS_LOG.read_text())[-10:]
	summary = '\n'.join(f"Task: {r['task']}\nResult: {r['result'][:200]}\nSteps: {r['steps']}" for r in runs)
	llm = ChatGoogle(model=model)
	from langchain_core.messages import HumanMessage
	resp = await llm.ainvoke([HumanMessage(content=
		f'Analyze these recent browser agent runs and suggest concrete improvements to task prompts or strategies:\n\n{summary}'
	)])
	return resp.content


# ---------------------------------------------------------------------------
# Main task runner
# ---------------------------------------------------------------------------

async def _run_agent_task() -> tuple[str, int]:
	global _agent
	agent_history = await _agent.run()
	result = agent_history.final_result() or 'Done — check the browser.'
	steps = agent_history.number_of_steps() if hasattr(agent_history, 'number_of_steps') else 0
	return result, steps


async def send_task(task: str, api_key: str, model: str, history: list) -> tuple[list, str]:
	global _agent, _agent_task

	def msg(role: str, content: str) -> dict:
		return {'role': role, 'content': content}

	if not task.strip():
		return history, ''

	if not api_key.strip() and not os.getenv('GOOGLE_API_KEY'):
		return history + [msg('user', task), msg('assistant', 'Error: No Google API key.')], ''

	if api_key.strip():
		os.environ['GOOGLE_API_KEY'] = api_key

	if not _chrome_is_ready():
		return history + [msg('user', task), msg('assistant', 'Error: Chrome not running. Click Launch Chrome first.')], ''

	try:
		llm = ChatGoogle(model=model)

		if _agent is None:
			browser = Browser(cdp_url=f'http://localhost:{CHROME_DEBUG_PORT}', keep_alive=True)
			_agent = Agent(task=task, llm=llm, browser=browser)
		else:
			_agent.llm = llm
			_agent.add_new_task(task)

		_agent_task = asyncio.create_task(_run_agent_task())
		result, steps = await _agent_task
		_log_run(task, result, steps)
		return history + [msg('user', task), msg('assistant', result)], ''

	except asyncio.CancelledError:
		_agent = None
		return history + [msg('user', task), msg('assistant', 'Agent stopped.')], ''
	except Exception as e:
		_agent = None
		return history + [msg('user', task), msg('assistant', f'Error: {e}')], ''


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def create_ui():
	with gr.Blocks(title='Browser Use — Gemma', theme=gr.themes.Soft()) as interface:
		gr.Markdown('# Browser Use Frontend')

		# Chrome + agent controls
		with gr.Row():
			launch_btn = gr.Button('Launch Chrome', variant='secondary', scale=1)
			kill_btn = gr.Button('Stop Chrome', variant='stop', scale=1)
			pause_btn = gr.Button('⏸ Pause', scale=1)
			stop_btn = gr.Button('⏹ Stop Agent', variant='stop', scale=1)
			reset_btn = gr.Button('↺ Reset Agent', variant='secondary', scale=1)
			status = gr.Textbox(label='Status', interactive=False, scale=3)

		launch_btn.click(fn=launch_chrome, outputs=status)
		kill_btn.click(fn=kill_chrome, outputs=status)
		pause_btn.click(fn=pause_agent, outputs=status)
		stop_btn.click(fn=stop_agent, outputs=status)

		# Mid-run injection row (visible always; active when paused)
		with gr.Row():
			inject_box = gr.Textbox(label='Inject instruction (while paused)', scale=4,
			                        placeholder='Type a correction or new direction, then Resume…')
			resume_btn = gr.Button('▶ Resume', variant='primary', scale=1)

		resume_btn.click(fn=resume_with_instruction, inputs=inject_box, outputs=status)

		gr.Markdown('---')

		with gr.Row():
			# Left: config + skills
			with gr.Column(scale=1):
				api_key = gr.Textbox(label='Google API Key (optional if in .env)', type='password')
				model = gr.Dropdown(
					choices=['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
					label='Model', value='gemma-4-31b-it',
				)

				gr.Markdown('### Skill Library')
				skill_dropdown = gr.Dropdown(choices=list_skill_names(), label='Load a skill', interactive=True)
				load_skill_btn = gr.Button('Load into task box')
				skill_name_box = gr.Textbox(label='Skill name (to save current task as skill)', placeholder='e.g. browse_x_top_posts')
				save_skill_btn = gr.Button('Save as skill')
				skill_status = gr.Textbox(label='Skill status', interactive=False)

				gr.Markdown('### Self-Improvement')
				reflect_btn = gr.Button('Reflect on recent runs')
				reflect_out = gr.Textbox(label='Reflection', lines=6, interactive=False)

			# Right: chat
			with gr.Column(scale=2):
				chatbot = gr.Chatbot(label='Conversation', height=450)
				with gr.Row():
					task_input = gr.Textbox(label='Task / Follow-up', placeholder='What should the agent do?', scale=4)
					send_btn = gr.Button('Send', variant='primary', scale=1)

		# Wire up reset after chatbot is defined
		reset_btn.click(fn=reset_agent, outputs=[status, chatbot])

		# Skills
		load_skill_btn.click(fn=load_skill, inputs=skill_dropdown, outputs=task_input)
		save_skill_btn.click(fn=save_skill, inputs=[skill_name_box, task_input], outputs=skill_status)

		# Reflect
		reflect_btn.click(fn=reflect_on_runs, inputs=[api_key, model], outputs=reflect_out)

		# Send task
		send_btn.click(fn=send_task, inputs=[task_input, api_key, model, chatbot], outputs=[chatbot, task_input])
		task_input.submit(fn=send_task, inputs=[task_input, api_key, model, chatbot], outputs=[chatbot, task_input])

	return interface


if __name__ == '__main__':
	demo = create_ui()
	demo.launch(server_name='127.0.0.1', server_port=7860, share=False)
