import os
import subprocess
import time
import glob

import httpx
from dotenv import load_dotenv
import gradio as gr

from browser_use import Agent, Browser, ChatGoogle

load_dotenv()

CHROME_DEBUG_PORT = 9222
CHROME_USER_DATA = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'browseruse-chrome-profile')

_chrome_proc: subprocess.Popen | None = None
_agent: Agent | None = None


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
	global _chrome_proc, _agent
	_agent = None
	if _chrome_proc and _chrome_proc.poll() is None:
		_chrome_proc.terminate()
		_chrome_proc = None
		return 'Chrome stopped'
	_chrome_proc = None
	return 'Chrome was not running'


def reset_agent() -> tuple[str, list]:
	global _agent
	_agent = None
	return 'Agent reset — next task starts fresh', []


async def send_task(task: str, api_key: str, model: str, history: list) -> tuple[list, str]:
	global _agent

	if not task.strip():
		return history, ''

	if not api_key.strip() and not os.getenv('GOOGLE_API_KEY'):
		history = history + [{'role': 'user', 'content': task}, {'role': 'assistant', 'content': 'Error: No Google API key provided.'}]
		return history, ''

	if api_key.strip():
		os.environ['GOOGLE_API_KEY'] = api_key

	if not _chrome_is_ready():
		history = history + [{'role': 'user', 'content': task}, {'role': 'assistant', 'content': 'Error: Chrome is not running. Click Launch Chrome first.'}]
		return history, ''

	history = history + [{'role': 'user', 'content': task}]

	try:
		llm = ChatGoogle(model=model)

		if _agent is None:
			browser = Browser(cdp_url=f'http://localhost:{CHROME_DEBUG_PORT}')
			_agent = Agent(task=task, llm=llm, browser=browser)
		else:
			_agent.llm = llm
			_agent.add_new_task(task)

		agent_history = await _agent.run()
		result = agent_history.final_result() or 'Done — no text result extracted. Check the browser.'
		history = history + [{'role': 'assistant', 'content': result}]

	except Exception as e:
		_agent = None
		history = history + [{'role': 'assistant', 'content': f'Error: {e}'}]

	return history, ''


def create_ui():
	with gr.Blocks(title='Browser Use - Gemma Edition') as interface:
		gr.Markdown('# Browser Use Frontend')

		with gr.Row():
			launch_btn = gr.Button('Launch Chrome', variant='secondary')
			kill_btn = gr.Button('Stop Chrome', variant='stop')
			reset_btn = gr.Button('Reset Agent', variant='secondary')
			chrome_status = gr.Textbox(label='Status', interactive=False, scale=3)

		launch_btn.click(fn=launch_chrome, outputs=chrome_status)
		kill_btn.click(fn=kill_chrome, outputs=chrome_status)

		gr.Markdown('---')

		with gr.Row():
			with gr.Column(scale=1):
				api_key = gr.Textbox(label='Google API Key (optional if in .env)', type='password')
				model = gr.Dropdown(
					choices=['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
					label='LLM Model', value='gemma-4-31b-it',
				)
				gr.Markdown('*The agent remembers context between tasks. Click **Reset Agent** to start fresh.*')

			with gr.Column(scale=2):
				chatbot = gr.Chatbot(label='Conversation', height=400, type='messages', elem_id='chatbot')
				with gr.Row():
					task_input = gr.Textbox(
						label='Task / Follow-up',
						placeholder='What should the agent do next?',
						scale=4,
					)
					send_btn = gr.Button('Send', variant='primary', scale=1)

		reset_btn.click(fn=reset_agent, outputs=[chrome_status, chatbot])
		send_btn.click(
			fn=send_task,
			inputs=[task_input, api_key, model, chatbot],
			outputs=[chatbot, task_input],
		)
		task_input.submit(
			fn=send_task,
			inputs=[task_input, api_key, model, chatbot],
			outputs=[chatbot, task_input],
		)

	return interface


if __name__ == '__main__':
	demo = create_ui()
	demo.launch(server_name='127.0.0.1', server_port=7860, share=False)
