"""
NiceGUI front-end — multi-agent browser controller.
Each agent slot gets its own isolated Chrome instance (port 9222+N).
"""
import asyncio
import os

from nicegui import ui

from .chrome import chrome_port, launch_chrome, kill_all_chrome, NUM_AGENTS, slot_is_ready
from .pool import (
	pause_agent, stop_agent, resume_agent, reset_agent,
	send_task as _pool_send_task,
	hard_stop, _agent_tasks,
)
from .runs import reflect_on_runs
from .skills import load_skills, load_skill, save_skill


def build_ui() -> None:
	# Mutable cross-section references filled in during tab construction
	refs: dict = {}

	# ── Global CSS ──────────────────────────────────────────────────────────
	ui.add_head_html('''<style>
	  .user-msg  { background:#1e3a5f; border-left:3px solid #3b82f6;
	               padding:8px 12px; margin:3px 0; border-radius:4px; font-size:.9em; }
	  .agent-msg { background:#14532d; border-left:3px solid #22c55e;
	               padding:8px 12px; margin:3px 0; border-radius:4px;
	               font-size:.9em; white-space:pre-wrap; }
	</style>''')

	# ── Header ───────────────────────────────────────────────────────────────
	with ui.header(elevated=True).classes('bg-gray-900 items-center gap-4 px-6 py-3'):
		ui.label('🤖 Browser Agents').classes('text-white text-xl font-bold')
		ui.space()
		chrome_badge = ui.badge('Chrome: —', color='grey')

		async def do_launch():
			chrome_badge.set_text('Launching…')
			chrome_badge.props('color=orange')
			msg = await asyncio.get_event_loop().run_in_executor(None, launch_chrome)
			chrome_badge.set_text(msg[:60])
			chrome_badge.props('color=green')
			ui.notify(msg, type='positive')

		def do_kill():
			kill_all_chrome(hard_stop)
			chrome_badge.set_text('Stopped')
			chrome_badge.props('color=red')
			ui.notify('All Chrome instances stopped', type='warning')

		ui.button('Launch Chrome', on_click=do_launch, icon='open_in_browser').props('flat color=white')
		ui.button('Stop All',      on_click=do_kill,   icon='stop_circle').props('flat color=red')

	# ── Config + Skills + Reflect ────────────────────────────────────────────
	with ui.row().classes('w-full px-6 pt-4 gap-4 items-start'):

		# Config card
		with ui.card().classes('flex-1 dark'):
			ui.label('Config').classes('font-semibold')
			api_key_input = ui.input('Google API Key', password=True,
			                         value=os.getenv('GOOGLE_API_KEY', '')).classes('w-full')
			model_select = ui.select(
				['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
				value='gemma-4-31b-it', label='Model',
			).classes('w-full')

		# Skills card — uses refs['agent1_task'] which is filled in after tabs
		with ui.card().classes('flex-1 dark'):
			ui.label('Skills Library').classes('font-semibold')
			skill_choices = list(load_skills().keys())
			skill_select = ui.select(skill_choices, label='Skill', value=None).classes('w-full')
			skill_name_input = ui.input(label='Save as').classes('w-full')
			skill_status = ui.label('').classes('text-xs text-gray-400')

			def on_load_skill():
				name = skill_select.value
				if not name:
					ui.notify('Select a skill first', type='warning')
					return
				text = load_skill(name)
				if 'agent1_task' in refs:
					refs['agent1_task'].set_value(text)
				ui.notify(f'Loaded "{name}"', type='positive')

			def on_save_skill():
				name = skill_name_input.value.strip()
				prompt = refs['agent1_task'].value if 'agent1_task' in refs else ''
				status, updated_names = save_skill(name, prompt)
				skill_status.set_text(status)
				skill_select.options = updated_names
				skill_select.update()
				ui.notify(status, type='positive' if 'Saved' in status else 'warning')

			with ui.row().classes('gap-2'):
				ui.button('Load → Agent 1', on_click=on_load_skill).props('flat color=blue size=sm')
				ui.button('Save',           on_click=on_save_skill).props('flat color=green size=sm')

		# Reflect card
		with ui.card().classes('flex-1 dark'):
			ui.label('Self-Improvement').classes('font-semibold')
			reflect_out = ui.textarea(label='Reflection').classes('w-full').props('readonly rows=4')

			async def on_reflect():
				reflect_out.set_value('Analyzing…')
				try:
					result = await reflect_on_runs(api_key_input.value, model_select.value)
					reflect_out.set_value(result)
				except Exception as e:
					reflect_out.set_value(f'Error: {e}')
					ui.notify(str(e), type='negative')

			ui.button('Reflect', on_click=on_reflect, icon='auto_fix_high').props('flat color=purple')

	# ── Agent tabs ───────────────────────────────────────────────────────────
	with ui.tabs().classes('px-6 pt-4 text-white') as tabs:
		tab_refs = [ui.tab(f'Agent {i+1}  :{chrome_port(i)}') for i in range(NUM_AGENTS)]

	with ui.tab_panels(tabs, value=tab_refs[0]).classes('w-full px-6 pb-8'):
		for slot_id in range(NUM_AGENTS):
			with ui.tab_panel(tab_refs[slot_id]):
				with ui.card().classes('w-full dark'):

					# Status + controls
					with ui.row().classes('items-center gap-2 mb-2'):
						dot = ui.badge('Idle', color='blue')

						# Use default-arg binding to freeze slot_id in each closure
						def mk_pause(s=slot_id):
							def _(): ui.notify(pause_agent(s))
							return _

						def mk_stop(s=slot_id):
							def _(): ui.notify(stop_agent(s), type='warning')
							return _

						ui.button('⏸', on_click=mk_pause()).props('flat size=sm color=orange').tooltip('Pause')
						ui.button('⏹', on_click=mk_stop()).props('flat size=sm color=red').tooltip('Stop')
						ui.space()

					# Chat log
					scroll = ui.scroll_area().classes('w-full rounded').style(
						'height:380px; background:#111827; padding:12px;'
					)
					with scroll:
						chat_col = ui.column().classes('w-full gap-0')

					# Reset (needs chat_col + dot captured — defined after)
					def mk_reset(s=slot_id, col=chat_col, badge=dot):
						def _():
							reset_agent(s)
							col.clear()
							badge.set_text('Idle')
							badge.props('color=blue')
							ui.notify(f'Agent {s+1} reset')
						return _

					ui.button('↺ Reset', on_click=mk_reset()).props('flat size=sm color=grey')

					# Inject + Resume
					with ui.row().classes('w-full gap-2 mt-3'):
						inject_box = ui.input(label='Inject instruction (while paused)').classes('flex-1')

						def mk_resume(s=slot_id, inj=inject_box):
							async def _():
								msg = await resume_agent(s, inj.value)
								inj.set_value('')
								ui.notify(msg, type='positive')
							return _

						ui.button('▶ Resume', on_click=mk_resume()).props('color=green size=sm')

					# Task input + send
					with ui.row().classes('w-full gap-2 mt-2'):
						task_input = ui.input(
							label=f'Task / follow-up for Agent {slot_id+1}',
							placeholder='What should this agent do?',
						).classes('flex-1')

						if slot_id == 0:
							refs['agent1_task'] = task_input

						def mk_send(s=slot_id, ti=task_input, col=chat_col, badge=dot, sc=scroll):
							async def _():
								task = ti.value.strip()
								if not task:
									return
								with col:
									ui.html(f'<div class="user-msg"><b>You:</b> {task}</div>')
								ti.set_value('')
								badge.set_text('Running')
								badge.props('color=green')
								history, _ = await _pool_send_task(
									s, task, api_key_input.value, model_select.value, []
								)
								if history:
									last = history[-1]
									if last['role'] == 'assistant':
										content = last['content'].replace('<', '&lt;').replace('>', '&gt;')
										with col:
											ui.html(f'<div class="agent-msg"><b>Agent {s+1}:</b> {content}</div>')
								badge.set_text('Idle')
								badge.props('color=blue')
								sc.scroll_to(percent=1.0)
							return _

						send_fn = mk_send()
						ui.button('Send', on_click=send_fn).props('color=primary')
						task_input.on('keydown.enter', send_fn)
