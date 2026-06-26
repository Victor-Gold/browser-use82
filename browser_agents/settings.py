import json
from .config import SKILLS_FILE

_SETTINGS_FILE = SKILLS_FILE.parent / 'settings.json'


def load_settings() -> dict:
	if _SETTINGS_FILE.exists():
		return json.loads(_SETTINGS_FILE.read_text())
	return {}


def save_settings(data: dict) -> None:
	_SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def get_setting(key: str, default: str = '') -> str:
	return load_settings().get(key, default)


def set_setting(key: str, value: str) -> None:
	s = load_settings()
	s[key] = value
	save_settings(s)
