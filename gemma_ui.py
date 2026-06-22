import os

from dotenv import load_dotenv
import gradio as gr

from browser_use import Agent, Browser, ChatGoogle

load_dotenv()


async def run_browser_task(
	task: str,
	api_key: str,
	model: str,
	use_debug_browser: bool,
	debug_port: str,
) -> str:
	if not api_key.strip() and not os.getenv('GOOGLE_API_KEY'):
		return 'Error: Please provide a Google API key or ensure it is set in your .env file.'

	if api_key.strip():
		os.environ['GOOGLE_API_KEY'] = api_key

	try:
		browser = None
		if use_debug_browser:
			browser = Browser(cdp_url=f'http://localhost:{debug_port}')

		llm = ChatGoogle(model=model)
		agent = Agent(task=task, llm=llm, browser=browser)
		history = await agent.run()

		final_result = history.final_result()
		if final_result:
			return f'Result: {final_result}'
		return "Agent finished but didn't extract any specific text. Check your browser to see what it did!"

	except Exception as e:
		return f'Error: {str(e)}'


def create_ui():
	with gr.Blocks(title='Browser Use - Gemma Edition') as interface:
		gr.Markdown('# Browser Use Frontend')
		gr.Markdown('Powered by **Gemma 4 31B**. The agent autonomously browses until the task is complete.')

		with gr.Row():
			with gr.Column(scale=1):
				gr.Markdown('### Configuration')
				api_key = gr.Textbox(label='Google API Key (optional if already in .env)', type='password')
				model = gr.Dropdown(
					choices=['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro'],
					label='LLM Model',
					value='gemma-4-31b-it',
				)

				gr.Markdown('### Browser Settings')
				use_debug_browser = gr.Checkbox(
					label='Connect to existing Chrome (must be running with --remote-debugging-port)',
					value=False,
				)
				debug_port = gr.Textbox(label='Debug Port', value='9222')

			with gr.Column(scale=2):
				gr.Markdown('### Task')
				task = gr.Textbox(
					label='What should the agent do?',
					placeholder='E.g., Go to news.ycombinator.com and summarize the top 3 posts...',
					lines=4,
				)
				submit_btn = gr.Button('Run Task', variant='primary')

				gr.Markdown('### Result')
				output = gr.Textbox(label='Output', lines=10, interactive=False)

		submit_btn.click(
			fn=run_browser_task,
			inputs=[task, api_key, model, use_debug_browser, debug_port],
			outputs=output,
		)

	return interface


if __name__ == '__main__':
	demo = create_ui()
	demo.launch(server_name='127.0.0.1', server_port=7860, share=False)
