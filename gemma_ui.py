from dotenv import load_dotenv
load_dotenv()

from browser_agents.ui import create_ui

if __name__ == '__main__':
	demo = create_ui()
	demo.queue(default_concurrency_limit=None)
	demo.launch(server_name='127.0.0.1', server_port=7860, share=False)
