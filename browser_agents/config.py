import os
from pathlib import Path

NUM_AGENTS = 3
CHROME_BASE_PORT = 9222
CHROME_USER_DATA_BASE = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'browseruse-chrome-profile')
SKILLS_FILE = Path(__file__).parent.parent / 'skills.json'
RUNS_LOG = Path(__file__).parent.parent / 'runs_log.json'
