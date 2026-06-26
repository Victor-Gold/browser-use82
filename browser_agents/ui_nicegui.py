"""
NiceGUI front-end — multi-agent browser controller with orchestration.
"""
import asyncio
import os

from nicegui import ui

from .chrome import (
	chrome_port, launch_chrome, launch_slot, kill_all_chrome, kill_slot,
	NUM_AGENTS, slot_is_ready,
)
from .orchestrator import orchestrate
from .pool import (
	pause_agent, stop_agent, resume_agent, reset_agent,
	send_task as pool_send_task,
	hard_stop,
)
from .runs import reflect_on_runs
from .settings import get_setting, set_setting
from .skills import load_skills, load_skill, save_skill


def build_ui() -> None:
	# Cross-tab refs filled in during tab construction
	refs: dict = {}          # 'agent1_task', 'chat_cols', 'chat_scrolls'
	refs['chat_cols'] = {}   # slot_id -> ui.column
	refs['chat_scrolls'] = {}

	# ── CSS ─────────────────────────────────────────────────────────────────
	ui.add_head_html('''<style>
	  .user-msg  { background:#1e3a5f; border-left:3px solid #3b82f6;
	               padding:8px 12px; margin:3px 0; border-radius:4px; font-size:.88em; }
	  .agent-msg { background:#14532d; border-left:3px solid #22c55e;
	               padding:8px 12px; margin:3px 0; border-radius:4px;
	               font-size:.88em; white-space:pre-wrap; }
	  .orch-plan { background:#1e1b4b; border-left:3px solid #818cf8;
	               padding:8px 12px; margin:3px 0; border-radius:4px; font-size:.88em; }
	  .orch-synth{ background:#1c1917; border-left:3px solid #f59e0b;
	               padding:10px 14px; margin:6px 0; border-radius:4px;
	               font-size:.9em; white-space:pre-wrap; }
	</style>''')

	# ── Header ───────────────────────────────────────────────────────────────
	with ui.header(elevated=True).classes('bg-gray-900 items-center gap-4 px-6 py-3'):
		ui.label('🤖 Browser Agents').classes('text-white text-xl font-bold')
		ui.space()
		chrome_badge = ui.badge('Chrome: —', color='grey')

		async def do_launch_all():
			chrome_badge.set_text('Launching all…')
			chrome_badge.props('color=orange')
			msg = await asyncio.get_event_loop().run_in_executor(None, launch_chrome)
			chrome_badge.set_text(msg[:70])
			chrome_badge.props('color=green')
			ui.notify(msg, type='positive')

		def do_kill_all():
			kill_all_chrome(hard_stop)
			chrome_badge.set_text('All stopped')
			chrome_badge.props('color=red')
			ui.notify('All Chrome instances stopped', type='warning')

		ui.button('Launch All Chrome', on_click=do_launch_all, icon='open_in_browser').props('flat color=white')
		ui.button('Stop All',          on_click=do_kill_all,   icon='stop_circle').props('flat color=red')

	# ── Config / Skills / Reflect ────────────────────────────────────────────
	with ui.row().classes('w-full px-6 pt-4 gap-4 items-start'):

		with ui.card().classes('flex-1 dark'):
			ui.label('Config').classes('font-semibold mb-1')
			api_key_input = ui.input('Google API Key', password=True,
			                         value=os.getenv('GOOGLE_API_KEY', '')).classes('w-full')
			model_select = ui.select(
				['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
				value='gemma-4-31b-it', label='Model',
			).classes('w-full')
			context_input = ui.textarea(
				label='Default context (account, URLs, preferences)',
				placeholder='e.g. My X/Twitter handle is @victorrgold. My following page: https://x.com/victorrgold/following',
				value=get_setting('context'),
			).classes('w-full').props('rows=3')
			context_input.on('blur', lambda: set_setting('context', context_input.value))

		with ui.card().classes('flex-1 dark'):
			ui.label('Skills Library').classes('font-semibold mb-1')
			skill_choices = list(load_skills().keys())
			skill_select = ui.select(skill_choices, label='Skill', value=None).classes('w-full')
			skill_name_input = ui.input(label='Save as skill name').classes('w-full')
			skill_status = ui.label('').classes('text-xs text-gray-400')

			def on_load_skill():
				name = skill_select.value
				if not name:
					ui.notify('Select a skill first', type='warning')
					return
				if 'agent1_task' in refs:
					refs['agent1_task'].set_value(load_skill(name))
				ui.notify(f'Loaded "{name}"', type='positive')

			def on_save_skill():
				name = skill_name_input.value.strip()
				prompt = refs['agent1_task'].value if 'agent1_task' in refs else ''
				status, updated = save_skill(name, prompt)
				skill_status.set_text(status)
				skill_select.options = updated
				skill_select.update()
				ui.notify(status, type='positive' if 'Saved' in status else 'warning')

			with ui.row().classes('gap-2'):
				ui.button('Load → Agent 1', on_click=on_load_skill).props('flat color=blue size=sm')
				ui.button('Save',           on_click=on_save_skill).props('flat color=green size=sm')

		with ui.card().classes('flex-1 dark'):
			ui.label('Self-Improvement').classes('font-semibold mb-1')
			reflect_out = ui.textarea(label='Reflection').classes('w-full').props('readonly rows=4')

			async def on_reflect():
				reflect_out.set_value('Analyzing…')
				try:
					result = await reflect_on_runs(api_key_input.value, model_select.value)
					reflect_out.set_value(result)
				except Exception as e:
					reflect_out.set_value(f'Error: {e}')

			ui.button('Reflect on recent runs', on_click=on_reflect, icon='auto_fix_high').props('flat color=purple')

	ui.separator().classes('mx-6 my-2')

	# ── Tabs ─────────────────────────────────────────────────────────────────
	with ui.tabs().classes('px-6 text-white') as tabs:
		orch_tab = ui.tab('🧠 Orchestrator')
		agent_tab_refs = [ui.tab(f'Agent {i+1}  :{chrome_port(i)}') for i in range(NUM_AGENTS)]

	with ui.tab_panels(tabs, value=orch_tab).classes('w-full px-6 pb-8'):

		# ── Orchestrator panel ───────────────────────────────────────────────
		with ui.tab_panel(orch_tab):
			with ui.card().classes('w-full dark'):

				# Mutable orchestrator state
				orch_state: dict = {
					'running': False,
					'ask_future': None,    # asyncio.Future when waiting for user reply
					'injection': None,     # pending mid-run user message
				}

				with ui.row().classes('items-center gap-3 mb-2'):
					ui.label('🧠 Orchestrator').classes('font-semibold text-gray-200')
					ui.space()
					orch_status_badge = ui.badge('Idle', color='grey')

				# Full conversation log (thoughts + dispatches + results + user messages)
				orch_scroll = ui.scroll_area().classes('w-full rounded').style(
					'height:460px; background:#0d1117; padding:12px;'
				)
				with orch_scroll:
					orch_log = ui.column().classes('w-full gap-0')

				# Input row — serves as: start / inject / answer question
				with ui.row().classes('w-full gap-2 mt-3 items-end'):
					orch_input = ui.input(
						label='Goal / message',
						placeholder='Type a high-level goal to start, or send a message while running…',
					).classes('flex-1')
					orch_send_btn = ui.button('Start', icon='play_arrow').props('color=indigo')
					orch_stop_btn = ui.button('Stop', icon='stop').props('flat color=red size=sm')

				def _olog(html: str):
					with orch_log:
						ui.html(html)
					orch_scroll.scroll_to(percent=1.0)

				def _olog_user(msg: str):
					safe = msg.replace('<', '&lt;').replace('>', '&gt;')
					_olog(f'<div class="user-msg"><b>👤 You:</b> {safe}</div>')

				def _olog_thought(msg: str):
					safe = msg.replace('<', '&lt;').replace('>', '&gt;')
					_olog(f'<div class="orch-plan"><b>🧠 Orchestrator:</b> {safe}</div>')

				def _olog_question(msg: str):
					safe = msg.replace('<', '&lt;').replace('>', '&gt;')
					_olog(f'<div style="background:#2d1b69;border-left:3px solid #a78bfa;padding:8px 12px;margin:3px 0;border-radius:4px;font-size:.88em"><b>❓ Orchestrator asks:</b> {safe}</div>')

				def _olog_dispatch(slot_id: int, task: str):
					safe = task.replace('<', '&lt;').replace('>', '&gt;')
					_olog(f'<div class="user-msg"><b>→ Agent {slot_id+1}:</b> {safe}</div>')

				def _olog_result(slot_id: int, result: str):
					safe = result.replace('<', '&lt;').replace('>', '&gt;')
					_olog(f'<div class="agent-msg"><b>← Agent {slot_id+1}:</b> {safe}</div>')

				def _olog_finish(text: str):
					safe = text.replace('<', '&lt;').replace('>', '&gt;')
					_olog(f'<div class="orch-synth"><b>✅ Final answer:</b> {safe}</div>')

				# ── Send handler ─────────────────────────────────────────────
				async def on_orch_send():
					msg = orch_input.value.strip()
					if not msg:
						return
					orch_input.set_value('')

					# Case 1: orchestrator waiting for user answer
					if orch_state['ask_future'] and not orch_state['ask_future'].done():
						_olog_user(msg)
						orch_state['ask_future'].set_result(msg)
						orch_send_btn.set_text('Inject')
						orch_status_badge.set_text('Running')
						orch_status_badge.props('color=green')
						return

					# Case 2: orchestrator running — inject message
					if orch_state['running']:
						_olog_user(f'[Injected] {msg}')
						orch_state['injection'] = msg
						ui.notify('Message injected into next step', type='info')
						return

					# Case 3: start new orchestration
					orch_log.clear()
					orch_state['running'] = True
					orch_state['ask_future'] = None
					orch_state['injection'] = None
					orch_send_btn.set_text('Inject')
					orch_send_btn.props('color=teal')
					orch_stop_btn.props(remove='disable')
					orch_status_badge.set_text('Running')
					orch_status_badge.props('color=green')
					_olog_user(msg)

					def on_thought(text: str):
						orch_status_badge.set_text(text[:40])
						_olog_thought(text)

					def on_agent_start(slot_id: int, subtask: str):
						_olog_dispatch(slot_id, subtask)
						col = refs['chat_cols'].get(slot_id)
						if col:
							with col:
								safe = subtask.replace('<', '&lt;').replace('>', '&gt;')
								ui.html(f'<div class="user-msg"><b>[Orchestrator]</b> {safe}</div>')

					def on_agent_result(slot_id: int, _subtask: str, result: str):
						_olog_result(slot_id, result)
						col = refs['chat_cols'].get(slot_id)
						sc  = refs['chat_scrolls'].get(slot_id)
						if col:
							safe = result.replace('<', '&lt;').replace('>', '&gt;')
							with col:
								ui.html(f'<div class="agent-msg"><b>Agent {slot_id+1}:</b> {safe}</div>')
						if sc:
							sc.scroll_to(percent=1.0)

					def on_finish(text: str):
						_olog_finish(text)

					async def on_ask_user(question: str) -> str:
						_olog_question(question)
						orch_send_btn.set_text('Answer')
						orch_send_btn.props('color=amber')
						orch_status_badge.set_text('Waiting for you')
						orch_status_badge.props('color=orange')
						loop = asyncio.get_event_loop()
						future = loop.create_future()
						orch_state['ask_future'] = future
						await asyncio.sleep(0)
						return await future

					def get_injection() -> str | None:
						val = orch_state['injection']
						orch_state['injection'] = None
						return val

					try:
						await orchestrate(
							task=msg,
							api_key=api_key_input.value,
							model=model_select.value,
							on_thought=on_thought,
							on_agent_start=on_agent_start,
							on_agent_result=on_agent_result,
							on_finish=on_finish,
							on_ask_user=on_ask_user,
							skills=load_skills(),
							context=context_input.value,
							get_injection=get_injection,
						)
						orch_status_badge.set_text('Done')
						orch_status_badge.props('color=blue')
					except asyncio.CancelledError:
						orch_status_badge.set_text('Stopped')
						orch_status_badge.props('color=grey')
					except Exception as e:
						_olog(f'<div style="color:#f87171;padding:6px">Error: {e}</div>')
						orch_status_badge.set_text('Error')
						orch_status_badge.props('color=red')
					finally:
						orch_state['running'] = False
						orch_state['ask_future'] = None
						orch_send_btn.set_text('Start')
						orch_send_btn.props('color=indigo')
						orch_stop_btn.props('disable')

				def on_orch_stop():
					if orch_state['ask_future'] and not orch_state['ask_future'].done():
						orch_state['ask_future'].cancel()
					orch_state['running'] = False
					ui.notify('Orchestrator stopped', type='warning')

				orch_send_btn.on('click', on_orch_send)
				orch_input.on('keydown.enter', on_orch_send)
				orch_stop_btn.on('click', on_orch_stop)
				orch_stop_btn.props('disable')

		# ── Per-agent panels ──────────────────────────────────────────────────
		for slot_id in range(NUM_AGENTS):
			with ui.tab_panel(agent_tab_refs[slot_id]):
				with ui.card().classes('w-full dark'):

					# Chrome status + per-slot controls
					with ui.row().classes('items-center gap-2 mb-1'):
						slot_badge = ui.badge('—', color='grey').tooltip(f'Port {chrome_port(slot_id)}')

						def mk_refresh_badge(s=slot_id, badge=slot_badge):
							def _():
								if slot_is_ready(s):
									badge.set_text(f'port {chrome_port(s)}')
									badge.props('color=green')
								else:
									badge.set_text('no Chrome')
									badge.props('color=red')
							return _

						def mk_launch_slot(s=slot_id, badge=slot_badge):
							async def _():
								badge.set_text('launching…')
								badge.props('color=orange')
								msg = await asyncio.get_event_loop().run_in_executor(
									None, lambda: launch_slot(s)
								)
								mk_refresh_badge(s, badge)()
								ui.notify(msg, type='positive' if 'ready' in msg.lower() else 'negative')
							return _

						def mk_kill_slot(s=slot_id, badge=slot_badge):
							def _():
								hard_stop(s)
								kill_slot(s)
								badge.set_text('stopped')
								badge.props('color=red')
								ui.notify(f'Agent {s+1} Chrome stopped', type='warning')
							return _

						ui.button('▶ Chrome', on_click=mk_launch_slot(), icon='open_in_browser').props('flat size=sm color=teal').tooltip('Launch this Chrome')
						ui.button('■',        on_click=mk_kill_slot(),   icon='cancel').props('flat size=sm color=red').tooltip('Kill this Chrome')
						ui.space()

						def mk_pause(s=slot_id):
							def _(): ui.notify(pause_agent(s))
							return _

						def mk_stop(s=slot_id):
							def _(): ui.notify(stop_agent(s), type='warning')
							return _

						ui.button('⏸', on_click=mk_pause()).props('flat size=sm color=orange').tooltip('Pause agent')
						ui.button('⏹', on_click=mk_stop()).props('flat size=sm color=red').tooltip('Stop agent')

					# Chat log
					scroll = ui.scroll_area().classes('w-full rounded').style(
						'height:360px; background:#111827; padding:12px;'
					)
					with scroll:
						chat_col = ui.column().classes('w-full gap-0')

					refs['chat_cols'][slot_id] = chat_col
					refs['chat_scrolls'][slot_id] = scroll

					# Reset
					def mk_reset(s=slot_id, col=chat_col, badge=slot_badge):
						def _():
							reset_agent(s)
							col.clear()
							badge.set_text('reset')
							ui.notify(f'Agent {s+1} reset')
						return _

					ui.button('↺ Reset', on_click=mk_reset()).props('flat size=sm color=grey')

					# Inject + Resume
					with ui.row().classes('w-full gap-2 mt-3'):
						inject_box = ui.input(label='Inject instruction while paused').classes('flex-1')

						def mk_resume(s=slot_id, inj=inject_box):
							async def _():
								msg = await resume_agent(s, inj.value)
								inj.set_value('')
								ui.notify(msg, type='positive')
							return _

						ui.button('▶ Resume', on_click=mk_resume()).props('color=green size=sm')

					# Task send
					with ui.row().classes('w-full gap-2 mt-2'):
						task_input = ui.input(
							label=f'Task / follow-up for Agent {slot_id+1}',
							placeholder='What should this agent do?',
						).classes('flex-1')

						if slot_id == 0:
							refs['agent1_task'] = task_input

						def mk_send(s=slot_id, ti=task_input, col=chat_col, sc=scroll):
							async def _():
								task = ti.value.strip()
								if not task:
									return
								with col:
									ui.html(f'<div class="user-msg"><b>You:</b> {task}</div>')
								ti.set_value('')
								history, _ = await pool_send_task(
									s, task, api_key_input.value, model_select.value, []
								)
								if history and history[-1]['role'] == 'assistant':
									content = history[-1]['content'].replace('<', '&lt;').replace('>', '&gt;')
									with col:
										ui.html(f'<div class="agent-msg"><b>Agent {s+1}:</b> {content}</div>')
								sc.scroll_to(percent=1.0)
							return _

						send_fn = mk_send()
						ui.button('Send', on_click=send_fn).props('color=primary')
						task_input.on('keydown.enter', send_fn)
