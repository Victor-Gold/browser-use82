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
				ui.label('Orchestrator — give a high-level goal, agents converge on it').classes('font-semibold mb-2 text-gray-300')

				with ui.row().classes('w-full gap-3 items-end'):
					orch_task_input = ui.input(
						label='High-level goal',
						placeholder='e.g. Research the top 3 AI companies and compare their products',
					).classes('flex-1')
					orch_run_btn = ui.button('Orchestrate', icon='hub').props('color=indigo')

				orch_status = ui.label('Idle').classes('text-sm text-gray-400 mt-2')

				# Live reasoning log
				ui.label('Reasoning log').classes('text-xs text-gray-500 mt-3')
				orch_log_scroll = ui.scroll_area().classes('w-full rounded').style(
					'height:160px; background:#0d1117; padding:10px;'
				)
				with orch_log_scroll:
					orch_log_col = ui.column().classes('w-full gap-0')

				# Final answer
				ui.label('Final answer').classes('text-xs text-gray-500 mt-3')
				orch_synth_scroll = ui.scroll_area().classes('w-full rounded').style(
					'height:160px; background:#111827; padding:10px;'
				)
				with orch_synth_scroll:
					orch_synth_col = ui.column().classes('w-full')

				async def run_orchestrator():
					task = orch_task_input.value.strip()
					if not task:
						ui.notify('Enter a goal first', type='warning')
						return
					orch_run_btn.props('disable')
					orch_log_col.clear()
					orch_synth_col.clear()
					orch_status.set_text('Running…')

					def _log(html: str):
						with orch_log_col:
							ui.html(html)
						orch_log_scroll.scroll_to(percent=1.0)

					def on_thought(msg: str):
						orch_status.set_text(msg)
						_log(f'<div class="orch-plan"><b>🧠 Orchestrator:</b> {msg}</div>')

					def on_agent_start(slot_id: int, subtask: str):
						safe = subtask.replace('<', '&lt;').replace('>', '&gt;')
						_log(f'<div class="user-msg"><b>→ Agent {slot_id+1} dispatched:</b> {safe}</div>')

					def on_agent_result(slot_id: int, subtask: str, result: str):
						safe = result.replace('<', '&lt;').replace('>', '&gt;')
						_log(f'<div class="agent-msg"><b>← Agent {slot_id+1} returned:</b> {safe}</div>')
						# Mirror into the agent's own tab
						col = refs['chat_cols'].get(slot_id)
						sc  = refs['chat_scrolls'].get(slot_id)
						if col:
							with col:
								ui.html(f'<div class="user-msg"><b>[Orchestrator]</b> {subtask.replace("<","&lt;").replace(">","&gt;")}</div>')
								ui.html(f'<div class="agent-msg"><b>Agent {slot_id+1}:</b> {safe}</div>')
						if sc:
							sc.scroll_to(percent=1.0)

					def on_finish(text: str):
						safe = text.replace('<', '&lt;').replace('>', '&gt;')
						orch_synth_col.clear()
						with orch_synth_col:
							ui.html(f'<div class="orch-synth">{safe}</div>')
						orch_synth_scroll.scroll_to(percent=1.0)

					try:
						await orchestrate(
							task=task,
							api_key=api_key_input.value,
							model=model_select.value,
							on_thought=on_thought,
							on_agent_start=on_agent_start,
							on_agent_result=on_agent_result,
							on_finish=on_finish,
						)
						orch_status.set_text('Done.')
					except Exception as e:
						orch_status.set_text(f'Error: {e}')
						ui.notify(str(e), type='negative')
					finally:
						orch_run_btn.props(remove='disable')

				orch_run_btn.on('click', run_orchestrator)

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

						async def mk_launch_slot(s=slot_id, badge=slot_badge):
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
