import asyncio
import os

from browser_use import Agent, Browser, ChatGoogle

from .chrome import chrome_port, slot_is_ready, launch_slot
from .config import NUM_AGENTS
from .runs import log_run

_agents: dict[int, Agent | None] = {i: None for i in range(NUM_AGENTS)}
_agent_tasks: dict[int, asyncio.Task | None] = {i: None for i in range(NUM_AGENTS)}


def pause_agent(slot_id: int) -> str:
	agent, task = _agents[slot_id], _agent_tasks[slot_id]
	if agent and task and not task.done():
		agent.pause()
		return f'Agent {slot_id+1} paused'
	return f'Agent {slot_id+1} not running'


def stop_agent(slot_id: int) -> str:
	agent, task = _agents[slot_id], _agent_tasks[slot_id]
	if agent and task and not task.done():
		agent.stop()
		return f'Agent {slot_id+1} stopped'
	return f'Agent {slot_id+1} not running'


def hard_stop(slot_id: int) -> None:
	"""Stop and clear agent state — used by chrome kill."""
	agent, task = _agents[slot_id], _agent_tasks[slot_id]
	if agent and task and not task.done():
		agent.stop()
	_agents[slot_id] = None
	_agent_tasks[slot_id] = None


async def resume_agent(slot_id: int, instruction: str) -> str:
	agent = _agents[slot_id]
	if agent is None:
		return f'Agent {slot_id+1} not initialized'
	if instruction.strip():
		agent.add_new_task(instruction)
	agent.resume()
	return f'Agent {slot_id+1} resumed'


def reset_agent(slot_id: int) -> tuple[str, list]:
	hard_stop(slot_id)
	return f'Agent {slot_id+1} reset', []


async def _run_agent(slot_id: int) -> tuple[str, int]:
	history = await _agents[slot_id].run()
	result = history.final_result() or 'Done — check the browser.'
	steps = getattr(history, 'number_of_steps', lambda: 0)()
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
	if not slot_is_ready(slot_id):
		# Auto-launch this slot's Chrome rather than hard-failing
		launch_msg = await asyncio.get_event_loop().run_in_executor(None, lambda: launch_slot(slot_id))
		if not slot_is_ready(slot_id):
			return history + [msg('user', task), msg('assistant', f'Error: {launch_msg}')], ''

	try:
		llm = ChatGoogle(model=model)

		if _agents[slot_id] is None:
			browser = Browser(cdp_url=f'http://localhost:{chrome_port(slot_id)}', keep_alive=True)
			_agents[slot_id] = Agent(task=task, llm=llm, browser=browser, max_failures=50)
		else:
			_agents[slot_id].llm = llm
			_agents[slot_id].add_new_task(task)

		_agent_tasks[slot_id] = asyncio.create_task(_run_agent(slot_id))
		result, _ = await _agent_tasks[slot_id]
		log_run(slot_id, task, result)
		return history + [msg('user', task), msg('assistant', result)], ''

	except asyncio.CancelledError:
		_agents[slot_id] = None
		return history + [msg('user', task), msg('assistant', 'Stopped.')], ''
	except Exception as e:
		_agents[slot_id] = None
		return history + [msg('user', task), msg('assistant', f'Error: {e}')], ''
