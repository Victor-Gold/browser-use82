import os
from pathlib import Path

NUM_AGENTS = 3
CHROME_BASE_PORT = 9222
CHROME_USER_DATA_BASE = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'browseruse-chrome-profile')
SKILLS_DIR = Path(__file__).parent.parent / 'skills'
RUNS_LOG = Path(__file__).parent.parent / 'runs_log.json'
RESULTS_DIR = Path(__file__).parent.parent / 'results'
