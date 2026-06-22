from functools import partial

import gradio as gr

from .chrome import chrome_port, launch_chrome, kill_all_chrome, NUM_AGENTS
from .pool import pause_agent, stop_agent, resume_agent, reset_agent, send_task, hard_stop
from .runs import reflect_on_runs
from .skills import load_skills, load_skill, save_skill


def _kill_chrome_ui() -> str:
	return kill_all_chrome(hard_stop)


def _save_skill_ui(name: str, prompt: str) -> tuple[str, gr.Dropdown]:
	status, skill_names = save_skill(name, prompt)
	return status, gr.Dropdown(choices=skill_names)


def create_ui() -> gr.Blocks:
	skill_names = list(load_skills().keys())

	with gr.Blocks(title='Browser Use — Multi-Agent') as interface:
		gr.Markdown('# Browser Use — Multi-Agent')

		# Chrome controls
		with gr.Row():
			launch_btn = gr.Button(f'Launch Chrome × {NUM_AGENTS}', variant='secondary')
			kill_btn = gr.Button('Stop All', variant='stop')
			chrome_status = gr.Textbox(label='Chrome Status', interactive=False, scale=3)

		launch_btn.click(fn=launch_chrome, outputs=chrome_status)
		kill_btn.click(fn=_kill_chrome_ui, outputs=chrome_status)

		# Config + skills row
		with gr.Row():
			with gr.Column(scale=1):
				api_key = gr.Textbox(label='Google API Key (optional if in .env)', type='password')
				model = gr.Dropdown(
					choices=['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
					label='Model', value='gemma-4-31b-it',
				)
			with gr.Column(scale=1):
				skill_dropdown = gr.Dropdown(choices=skill_names, label='Load skill into Agent 1 task box')
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

		agent_task_inputs: list[gr.Textbox] = []

		with gr.Tabs():
			for slot_id in range(NUM_AGENTS):
				with gr.Tab(label=f'Agent {slot_id + 1}  (port {chrome_port(slot_id)})'):
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

					p_btn.click(fn=partial(pause_agent, slot_id), outputs=slot_status)
					s_btn.click(fn=partial(stop_agent, slot_id), outputs=slot_status)
					r_btn.click(fn=partial(reset_agent, slot_id), outputs=[slot_status, chatbot])
					resume_btn.click(
						fn=partial(resume_agent, slot_id),
						inputs=inject_box, outputs=slot_status,
						concurrency_limit=None,
					)
					send_btn.click(
						fn=partial(send_task, slot_id),
						inputs=[task_input, api_key, model, chatbot],
						outputs=[chatbot, task_input],
						concurrency_limit=None,
					)
					task_input.submit(
						fn=partial(send_task, slot_id),
						inputs=[task_input, api_key, model, chatbot],
						outputs=[chatbot, task_input],
						concurrency_limit=None,
					)

		# Skill controls target Agent 1's task box; saving also refreshes the dropdown
		if agent_task_inputs:
			load_skill_btn.click(fn=load_skill, inputs=skill_dropdown, outputs=agent_task_inputs[0])
			save_skill_btn.click(
				fn=_save_skill_ui,
				inputs=[skill_name_box, agent_task_inputs[0]],
				outputs=[skill_status, skill_dropdown],
			)

	return interface
