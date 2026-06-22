import glob
import os
import subprocess
import time

import httpx

from .config import CHROME_BASE_PORT, CHROME_USER_DATA_BASE, NUM_AGENTS

_chrome_procs: dict[int, subprocess.Popen | None] = {i: None for i in range(NUM_AGENTS)}


def chrome_port(slot_id: int) -> int:
	return CHROME_BASE_PORT + slot_id


def chrome_user_data(slot_id: int) -> str:
	return f'{CHROME_USER_DATA_BASE}-{slot_id}'


def slot_is_ready(slot_id: int) -> bool:
	try:
		r = httpx.get(f'http://127.0.0.1:{chrome_port(slot_id)}/json/version', timeout=1.0)
		return r.status_code == 200
	except Exception:
		return False


def find_chrome() -> str:
	localappdata = os.environ.get('LOCALAPPDATA', '')
	patterns = [
		os.path.join(localappdata, r'ms-playwright\chromium-*\chrome-win64\chrome.exe'),
		os.path.join(localappdata, r'ms-playwright\chromium-*\chrome-win\chrome.exe'),
		r'C:\Program Files\Google\Chrome\Application\chrome.exe',
		r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
		os.path.join(localappdata, r'Google\Chrome\Application\chrome.exe'),
	]
	for pattern in patterns:
		matches = glob.glob(pattern)
		if matches:
			return sorted(matches)[-1]
		if os.path.isfile(pattern):
			return pattern
	raise RuntimeError('No Chrome/Chromium found.')


def launch_chrome() -> str:
	chrome = find_chrome()
	for slot_id in range(NUM_AGENTS):
		if slot_is_ready(slot_id):
			continue
		port = chrome_port(slot_id)
		user_data = chrome_user_data(slot_id)
		os.makedirs(user_data, exist_ok=True)
		_chrome_procs[slot_id] = subprocess.Popen(
			[chrome, f'--remote-debugging-port={port}',
			 f'--user-data-dir={user_data}',
			 '--no-first-run', '--no-default-browser-check'],
			stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
		)
	for _ in range(15):
		time.sleep(1)
		if all(slot_is_ready(i) for i in range(NUM_AGENTS)):
			break
	ready = [str(chrome_port(i)) for i in range(NUM_AGENTS) if slot_is_ready(i)]
	failed = [str(chrome_port(i)) for i in range(NUM_AGENTS) if not slot_is_ready(i)]
	status = f'Ready on ports: {", ".join(ready)}'
	if failed:
		status += f' | Failed: {", ".join(failed)}'
	return status


def kill_all_chrome(stop_agent_fn) -> str:
	"""stop_agent_fn(slot_id) is called before killing Chrome for each slot."""
	for i in range(NUM_AGENTS):
		stop_agent_fn(i)
		proc = _chrome_procs[i]
		if proc and proc.poll() is None:
			proc.terminate()
		_chrome_procs[i] = None
	return 'All Chrome instances stopped'
