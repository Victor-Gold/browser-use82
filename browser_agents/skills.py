import json

from .config import SKILLS_FILE


def load_skills() -> dict[str, str]:
	if SKILLS_FILE.exists():
		return json.loads(SKILLS_FILE.read_text())
	return {}


def load_skill(name: str) -> str:
	return load_skills().get(name, '')


def save_skill(name: str, prompt: str) -> tuple[str, list[str]]:
	"""Returns (status_message, updated_skill_names_list)."""
	if not name.strip() or not prompt.strip():
		return 'Provide both a name and a prompt', list(load_skills().keys())
	skills = load_skills()
	skills[name.strip()] = prompt.strip()
	SKILLS_FILE.write_text(json.dumps(skills, indent=2))
	return f'Saved "{name.strip()}"', list(skills.keys())
