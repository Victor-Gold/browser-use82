from dotenv import load_dotenv
load_dotenv()

from nicegui import ui
from browser_agents.ui_nicegui import build_ui

build_ui()
ui.run(host='127.0.0.1', port=7860, title='Browser Agents', dark=True, reload=False)
