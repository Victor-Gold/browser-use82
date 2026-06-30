import json
import re
from pathlib import Path

from .config import SKILLS_DIR


def load_skills() -> dict[str, str]:
	skills = {}
	if not SKILLS_DIR.exists():
		return skills

	for skill_folder in SKILLS_DIR.iterdir():
		if skill_folder.is_dir():
			skill_file = skill_folder / 'SKILL.md'
			if skill_file.exists():
				content = skill_file.read_text(encoding='utf-8')
				name = skill_folder.name

				# Extract frontmatter if present
				match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
				if match:
					frontmatter = match.group(1)
					name_match = re.search(r'^name:\s*(.+)$', frontmatter, re.MULTILINE)
					if name_match:
						name = name_match.group(1).strip()
					prompt = content[match.end():].strip()
					skills[name] = prompt
				else:
					skills[name] = content.strip()
	return skills


def load_skill(name: str) -> str:
	return load_skills().get(name, '')


def save_skill(name: str, prompt: str) -> tuple[str, list[str]]:
	"""Returns (status_message, updated_skill_names_list)."""
	if not name.strip() or not prompt.strip():
		return 'Provide both a name and a prompt', list(load_skills().keys())
	
	SKILLS_DIR.mkdir(parents=True, exist_ok=True)
	
	folder_name = re.sub(r'[^a-zA-Z0-9_-]', '-', name.strip().lower())
	skill_folder = SKILLS_DIR / folder_name
	skill_folder.mkdir(exist_ok=True)
	
	skill_file = skill_folder / 'SKILL.md'
	content = f"---\nname: {name.strip()}\n---\n\n{prompt.strip()}\n"
	skill_file.write_text(content, encoding='utf-8')
	
	return f'Saved "{name.strip()}"', list(load_skills().keys())
