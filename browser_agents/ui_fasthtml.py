"""
FastHTML front-end — multi-agent browser controller with orchestration.

Server-rendered with HTMX. A single WebSocket (`/ws`) carries every live update
(orchestrator stream + agent chat), while one-shot actions (chrome control,
skills, config, pause/stop/reset) are plain HTMX POST routes that return
out-of-band fragments. The heavy lifting still lives in the unchanged backend
modules (pool / orchestrator / chrome / skills / runs / settings).
"""
import asyncio
import os

from fasthtml.common import (
	fast_app, Style, Script, Title,
	Div, Span, Section, Header, Footer, Form, Label, Strong, Pre,
	Button, Input, Textarea, Select, Option,
)

from .chrome import (
	chrome_port, launch_chrome, launch_slot, kill_all_chrome, kill_slot,
	NUM_AGENTS, slot_is_ready,
)
from .orchestrator import orchestrate
from .pool import (
	pause_agent, stop_agent, resume_agent, reset_agent,
	send_task as pool_send_task, hard_stop,
)
from .runs import reflect_on_runs
from .settings import get_setting, set_setting
from .skills import load_skills, load_skill, save_skill

MODELS = ['gemma-4-31b-it', 'gemini-2.5-flash', 'gemini-2.5-pro']

# Server-side config shared with the background tasks (the API key stays in
# memory only; context is also persisted to settings.json).
CONFIG: dict[str, str] = {
	'api_key': os.getenv('GOOGLE_API_KEY', ''),
	'model': MODELS[0],
	'context': get_setting('context'),
}

# Live orchestrator state (single-user, local tool — module globals are fine).
ORCH: dict = {'running': False, 'ask_future': None, 'injection': None, 'task_obj': None}


# ── WebSocket connection registry + broadcast ───────────────────────────────
_clients: dict = {}  # ws -> send callable


async def _on_conn(ws, send):
	_clients[ws] = send


async def _on_disconn(ws):
	_clients.pop(ws, None)


async def _broadcast(*parts) -> None:
	for ws, send in list(_clients.items()):
		try:
			for p in parts:
				await send(p)
		except Exception:
			_clients.pop(ws, None)


def _emit(*parts) -> None:
	"""Fire-and-forget broadcast from a synchronous callback context."""
	asyncio.create_task(_broadcast(*parts))


# ── OOB fragment builders ───────────────────────────────────────────────────
def _statusline(text: str = '') -> Span:
	return Span(text, id='statusline', cls='statusline', hx_swap_oob='true')


def _chrome_badge(text: str, color: str = 'grey') -> Span:
	return Span(text, id='chrome-badge', cls=f'badge {color}', hx_swap_oob='true')


def _slot_badge(s: int, text: str, color: str) -> Span:
	return Span(text, id=f'slot-badge-{s}', cls=f'badge {color}', hx_swap_oob='true')


def _orch_status(text: str, color: str) -> Span:
	return Span(text, id='orch-status', cls=f'badge {color}', hx_swap_oob='true')


def _append(target_id: str, *children) -> Div:
	"""Append children to #target_id (HTMX out-of-band beforeend swap)."""
	return Div(*children, id=target_id, hx_swap_oob='beforeend')


def _clear(target_id: str) -> Div:
	return Div(id=target_id, hx_swap_oob='innerHTML')


def _bubble(kind: str, who: str, text: str) -> Div:
	return Div(Div(Strong(who), ' ', Span(text), cls='msg-body'), cls=f'msg {kind}')


def _skill_select(oob: bool = False) -> Select:
	names = list(load_skills().keys())
	opts = [Option('— select a skill —', value='', selected=True)] + [Option(n, value=n) for n in names]
	attrs = {'hx_swap_oob': 'true'} if oob else {}
	return Select(*opts, id='skill-select', name='skill', cls='input', **attrs)


def _agent_task_input(s: int, value: str = '', oob: bool = False) -> Textarea:
	attrs = {'hx_swap_oob': 'true'} if oob else {}
	return Textarea(
		value, name='task', id=f'agent-{s}-task', rows='2',
		placeholder=f'Task / follow-up for Agent {s + 1}…', cls='input', **attrs,
	)


# ── Background runners ──────────────────────────────────────────────────────
async def _run_agent_send(s: int, task: str) -> None:
	try:
		history, _ = await pool_send_task(s, task, CONFIG['api_key'], CONFIG['model'], [])
		content = history[-1]['content'] if history and history[-1]['role'] == 'assistant' else 'No result.'
	except Exception as e:  # noqa: BLE001 — surface any failure in the chat
		content = f'Error: {e}'
	await _broadcast(_append(f'chat-{s}-inner', _bubble('agent', f'Agent {s + 1}:', content)))


async def _run_orch(goal: str) -> None:
	def on_thought(text: str) -> None:
		_emit(_orch_status(text[:36], 'green'),
		      _append('orch-log-inner', _bubble('thought', '🧠 Orchestrator:', text)))

	def on_agent_start(s: int, subtask: str) -> None:
		_emit(_append('orch-log-inner', _bubble('user', f'→ Agent {s + 1}:', subtask)),
		      _append(f'chat-{s}-inner', _bubble('user', '[Orchestrator]', subtask)))

	def on_agent_result(s: int, _sub: str, result: str) -> None:
		_emit(_append('orch-log-inner', _bubble('agent', f'← Agent {s + 1}:', result)),
		      _append(f'chat-{s}-inner', _bubble('agent', f'Agent {s + 1}:', result)))

	def on_finish(text: str) -> None:
		_emit(_append('orch-log-inner', _bubble('finish', '✅ Final answer:', text)))

	async def on_ask_user(question: str) -> str:
		_emit(_append('orch-log-inner', _bubble('question', '❓ Orchestrator asks:', question)),
		      _orch_status('Waiting for you', 'orange'))
		fut = asyncio.get_event_loop().create_future()
		ORCH['ask_future'] = fut
		return await fut

	def get_injection() -> str | None:
		val = ORCH['injection']
		ORCH['injection'] = None
		return val

	try:
		await orchestrate(
			task=goal, api_key=CONFIG['api_key'], model=CONFIG['model'],
			on_thought=on_thought, on_agent_start=on_agent_start,
			on_agent_result=on_agent_result, on_finish=on_finish,
			on_ask_user=on_ask_user, get_injection=get_injection,
			skills=load_skills(), context=CONFIG['context'],
		)
		_emit(_orch_status('Done', 'blue'))
	except asyncio.CancelledError:
		_emit(_orch_status('Stopped', 'grey'))
	except Exception as e:  # noqa: BLE001
		_emit(_append('orch-log-inner', Div(f'Error: {e}', cls='err')), _orch_status('Error', 'red'))
	finally:
		ORCH['running'] = False
		ORCH['ask_future'] = None


# ── Page layout ─────────────────────────────────────────────────────────────
def _header() -> Header:
	return Header(
		Div('🤖', Span('Browser Agents', cls='brand-name'), cls='brand'),
		Div(cls='grow'),
		_chrome_badge('Chrome: —', 'grey'),
		Button('Launch All', cls='btn btn-primary', hx_post='/chrome/launch-all', hx_swap='none'),
		Button('Stop All', cls='btn btn-danger', hx_post='/chrome/stop-all', hx_swap='none'),
		cls='topbar',
	)


def _control_row() -> Section:
	return Section(
		# Config
		Div(
			Div('Config', cls='card-title'),
			Form(
				Input(name='api_key', type='password', value=CONFIG['api_key'],
				      placeholder='Google API Key', cls='input'),
				Select(*[Option(m, value=m, selected=(m == CONFIG['model'])) for m in MODELS],
				       name='model', cls='input'),
				Textarea(CONFIG['context'], name='context', rows='3', cls='input',
				         placeholder='Default context — handles, URLs, preferences…'),
				hx_post='/config', hx_trigger='change', hx_swap='none', cls='stack',
			),
			cls='card',
		),
		# Skills
		Div(
			Div('Skills Library', cls='card-title'),
			_skill_select(),
			Input(name='skill_name', placeholder='Save current task as…', cls='input'),
			Div(
				Button('Load → Agent 1', cls='btn btn-ghost btn-sm',
				       hx_post='/skill/load', hx_include='#skill-select', hx_swap='none'),
				Button('Save', cls='btn btn-ghost btn-sm',
				       hx_post='/skill/save',
				       hx_include="[name='skill_name'], #agent-0-task", hx_swap='none'),
				cls='row gap',
			),
			cls='card',
		),
		# Self-improvement
		Div(
			Div('Self-Improvement', cls='card-title'),
			Button('Reflect on recent runs', cls='btn btn-ghost',
			       hx_post='/reflect', hx_target='#reflect-out', hx_swap='innerHTML',
			       hx_disabled_elt='this'),
			Pre('', id='reflect-out', cls='reflect'),
			cls='card',
		),
		cls='control-row',
	)


def _orchestrator_panel() -> Section:
	return Section(
		Div(
			Div(
				Span('🧠 Orchestrator', cls='card-title'),
				Div(cls='grow'),
				_orch_status('Idle', 'grey'),
				cls='row items-center',
			),
			Div(Div(id='orch-log-inner'), id='orch-log', cls='log scroll'),
			Form(
				Input(type='hidden', name='action', value='orch_submit'),
				Input(name='goal', id='orch-input', cls='input grow',
				      placeholder='Type a goal to start, or a message while running…',
				      autocomplete='off'),
				Button('Send', cls='btn btn-primary', type='submit'),
				ws_send='true', cls='row gap composer',
			),
			Form(
				Input(type='hidden', name='action', value='orch_stop'),
				Button('Stop', cls='btn btn-danger btn-sm', type='submit'),
				ws_send='true',
			),
			cls='card',
		),
		cls='panel', id='panel-orch',
	)


def _agent_panel(s: int) -> Section:
	return Section(
		Div(
			Div(
				_slot_badge(s, f'port {chrome_port(s)}', 'grey'),
				Button('▶ Chrome', cls='btn btn-ghost btn-sm',
				       hx_post=f'/chrome/launch/{s}', hx_swap='none'),
				Button('■ Kill', cls='btn btn-ghost btn-sm',
				       hx_post=f'/chrome/kill/{s}', hx_swap='none'),
				Div(cls='grow'),
				Button('⏸ Pause', cls='btn btn-ghost btn-sm',
				       hx_post=f'/agent/{s}/pause', hx_swap='none'),
				Button('⏹ Stop', cls='btn btn-ghost btn-sm',
				       hx_post=f'/agent/{s}/stop', hx_swap='none'),
				Button('↺ Reset', cls='btn btn-ghost btn-sm',
				       hx_post=f'/agent/{s}/reset', hx_swap='none'),
				cls='row gap items-center',
			),
			Div(Div(id=f'chat-{s}-inner'), id=f'chat-{s}', cls='log scroll'),
			# Inject while paused
			Form(
				Input(name='instruction', id=f'inject-{s}', cls='input grow',
				      placeholder='Inject an instruction while paused…', autocomplete='off'),
				Button('▶ Resume', cls='btn btn-ghost btn-sm',
				       hx_post=f'/agent/{s}/resume', hx_include=f'#inject-{s}', hx_swap='none'),
				cls='row gap',
			),
			# Task send (over WebSocket for live results)
			Form(
				Input(type='hidden', name='action', value='agent_send'),
				Input(type='hidden', name='slot', value=str(s)),
				_agent_task_input(s),
				Button('Send', cls='btn btn-primary', type='submit'),
				ws_send='true', cls='row gap composer',
			),
			cls='card',
		),
		cls='panel', id=f'panel-agent-{s}',
	)


def _tabs() -> Div:
	radios = [Input(type='radio', name='tab', id='tab-orch', cls='tabradio', checked=True)]
	labels = [Label('🧠 Orchestrator', _for='tab-orch', cls='tablabel')]
	for s in range(NUM_AGENTS):
		radios.append(Input(type='radio', name='tab', id=f'tab-agent-{s}', cls='tabradio'))
		labels.append(Label(f'Agent {s + 1}  :{chrome_port(s)}', _for=f'tab-agent-{s}', cls='tablabel'))
	panels = [_orchestrator_panel()] + [_agent_panel(s) for s in range(NUM_AGENTS)]
	return Div(*radios, Div(*labels, cls='tabbar'), Div(*panels, cls='panels'), cls='tabs')


def _page():
	return Title('Browser Agents'), Div(
		_header(),
		_control_row(),
		_tabs(),
		Footer(_statusline('Ready'), cls='footer'),
		cls='app',
	)


# ── App + routes ────────────────────────────────────────────────────────────
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=Rajdhani:wght@500;600;700&family=Inter:wght@400;500;600&display=swap');
:root{
  --bg:#04060c; --c1:#22d3ee; --c2:#a855f7; --c3:#f0abfc; --c-acc:#38bdf8;
  --text:#eaf2ff; --muted:#8aa0c0;
  --glass:rgba(20,30,54,.42); --glass2:rgba(12,20,38,.55);
  --line:rgba(120,170,255,.16); --line2:rgba(120,170,255,.30);
  --glow:rgba(34,211,238,.45); --glow2:rgba(168,85,247,.45);
  --ui:'Rajdhani','Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --body:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;min-height:100vh;color:var(--text);font:15px/1.55 var(--body);
  -webkit-font-smoothing:antialiased;background:var(--bg);position:relative;overflow-x:hidden}
/* aurora field */
body::before{content:"";position:fixed;inset:-20% -10% -10%;z-index:-2;pointer-events:none;
  background:
    radial-gradient(50% 45% at 80% 0%,rgba(34,211,238,.22),transparent 60%),
    radial-gradient(45% 45% at 12% 8%,rgba(168,85,247,.22),transparent 60%),
    radial-gradient(60% 50% at 50% 110%,rgba(56,189,248,.16),transparent 60%);
  filter:blur(20px);animation:drift 22s ease-in-out infinite alternate}
@keyframes drift{from{transform:translate3d(-2%,-1%,0) scale(1)}to{transform:translate3d(3%,2%,0) scale(1.08)}}
/* perspective grid */
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.5;
  background-image:linear-gradient(rgba(120,170,255,.06) 1px,transparent 1px),
    linear-gradient(90deg,rgba(120,170,255,.06) 1px,transparent 1px);
  background-size:46px 46px;
  -webkit-mask-image:radial-gradient(120% 80% at 50% 0%,#000 35%,transparent 78%);
  mask-image:radial-gradient(120% 80% at 50% 0%,#000 35%,transparent 78%)}
.app{max-width:1220px;margin:0 auto;padding:0 22px 56px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:linear-gradient(180deg,var(--c1),var(--c2));border-radius:8px;
  border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-track{background:transparent}

/* glass primitive */
.glass{background:var(--glass);backdrop-filter:blur(22px) saturate(150%);
  -webkit-backdrop-filter:blur(22px) saturate(150%);border:1px solid var(--line);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.08),inset 0 0 24px rgba(120,170,255,.04),
    0 18px 48px -24px rgba(0,0,0,.9)}

/* topbar */
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;
  padding:14px 18px;margin:14px 0 6px;border-radius:16px;
  background:var(--glass2);backdrop-filter:blur(22px) saturate(150%);
  -webkit-backdrop-filter:blur(22px) saturate(150%);border:1px solid var(--line);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 18px 48px -28px #000}
.brand{display:flex;align-items:center;gap:11px;font-size:22px}
.brand>span:first-child,.brand :first-child{font-size:24px;filter:drop-shadow(0 0 8px var(--glow))}
.brand-name{font-family:var(--ui);font-weight:800;letter-spacing:3px;text-transform:uppercase;
  font-size:20px;background:linear-gradient(90deg,var(--c1),var(--c3));
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
  filter:drop-shadow(0 0 14px var(--glow))}
.grow{flex:1}

/* badges */
.badge{display:inline-flex;align-items:center;gap:7px;padding:6px 13px;border-radius:999px;
  font-family:var(--ui);font-size:12.5px;font-weight:600;letter-spacing:.6px;white-space:nowrap;
  border:1px solid var(--line2);background:rgba(10,18,36,.5);backdrop-filter:blur(8px)}
.badge::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;
  box-shadow:0 0 10px currentColor,0 0 4px currentColor;animation:pulse 2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.45}}
.badge.grey{color:#9fb4d4}
.badge.green{color:#34f5c5;border-color:rgba(52,245,197,.4)}
.badge.red{color:#ff6e8a;border-color:rgba(255,110,138,.4)}
.badge.orange{color:#ffc24b;border-color:rgba(255,194,75,.4)}
.badge.blue{color:#5cc8ff;border-color:rgba(92,200,255,.4)}

/* buttons */
.btn{appearance:none;font-family:var(--ui);letter-spacing:.5px;border:1px solid var(--line2);
  background:rgba(20,32,58,.5);backdrop-filter:blur(8px);color:var(--text);
  padding:9px 16px;border-radius:11px;font-weight:600;font-size:13.5px;cursor:pointer;
  transition:transform .1s,box-shadow .2s,border-color .2s,background .2s}
.btn:hover{border-color:var(--c1);box-shadow:0 0 0 1px var(--glow),0 0 18px -4px var(--glow);
  background:rgba(34,211,238,.08)}
.btn:active{transform:translateY(1px)}
.btn-sm{padding:6px 12px;font-size:12.5px;border-radius:9px}
.btn-primary{border:1px solid transparent;color:#04121a;font-weight:700;
  background:linear-gradient(120deg,var(--c1),var(--c-acc) 55%,var(--c2));
  box-shadow:0 0 18px -2px var(--glow),0 0 28px -6px var(--glow2)}
.btn-primary:hover{filter:brightness(1.1);box-shadow:0 0 26px 0 var(--glow),0 0 40px -6px var(--glow2)}
.btn-ghost{background:rgba(20,32,58,.35)}
.btn-danger{color:#ff6e8a;border-color:rgba(255,110,138,.4);background:rgba(255,110,138,.06)}
.btn-danger:hover{box-shadow:0 0 18px -4px rgba(255,110,138,.6);border-color:#ff6e8a;
  background:rgba(255,110,138,.12)}
.btn.htmx-request,.btn:disabled{opacity:.5;pointer-events:none}

/* layout helpers */
.row{display:flex}.gap{gap:9px}.items-center{align-items:center}
.stack{display:flex;flex-direction:column;gap:11px}

/* cards */
.control-row{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:18px 0}
.card{position:relative;border-radius:18px;padding:20px;overflow:hidden;
  background:var(--glass);backdrop-filter:blur(22px) saturate(150%);
  -webkit-backdrop-filter:blur(22px) saturate(150%);border:1px solid var(--line);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 18px 48px -26px rgba(0,0,0,.9);
  transition:border-color .2s,transform .2s,box-shadow .2s}
/* neon top edge */
.card::before{content:"";position:absolute;top:0;left:18px;right:18px;height:1px;
  background:linear-gradient(90deg,transparent,var(--c1),var(--c2),transparent);opacity:.7}
.control-row .card{display:flex;flex-direction:column}
.control-row .card:hover{transform:translateY(-3px);border-color:var(--line2);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.1),0 24px 56px -24px rgba(0,0,0,.95),
    0 0 26px -10px var(--glow)}
.card-title{display:flex;align-items:center;gap:9px;font-family:var(--ui);font-weight:700;
  font-size:13px;text-transform:uppercase;letter-spacing:2px;color:#bcd0ef;margin-bottom:15px}
.card-title::before{content:"";width:4px;height:15px;border-radius:3px;
  background:linear-gradient(180deg,var(--c1),var(--c2));box-shadow:0 0 10px var(--glow)}

/* inputs */
.input{width:100%;background:rgba(6,12,26,.55);color:var(--text);border:1px solid var(--line2);
  border-radius:11px;padding:10px 13px;font:inherit;font-size:14px;outline:none;transition:.18s}
.input::placeholder{color:#5e739a}
select.input{font-family:var(--ui);letter-spacing:.4px}
.input:focus{border-color:var(--c1);box-shadow:0 0 0 3px rgba(34,211,238,.18),0 0 18px -6px var(--glow)}
textarea.input{resize:vertical}
.composer{align-items:flex-end}

.reflect{margin:14px 0 0;flex:1;min-height:92px;max-height:200px;overflow:auto;
  background:rgba(6,12,26,.5);border:1px solid var(--line2);border-radius:12px;padding:13px;
  white-space:pre-wrap;font-size:13px;color:var(--text)}
.reflect:empty::before{content:"// suggestions from your recent runs will surface here";color:#5e739a;
  font-family:var(--ui);letter-spacing:.5px}

/* tabs (pure CSS) */
.tabs{margin-top:10px}
.tabradio{display:none}
.tabbar{display:inline-flex;gap:6px;margin-bottom:18px;padding:6px;border-radius:14px;
  background:var(--glass2);backdrop-filter:blur(16px);border:1px solid var(--line)}
.tablabel{padding:9px 20px;cursor:pointer;color:var(--muted);font-family:var(--ui);font-weight:600;
  font-size:14px;letter-spacing:1px;text-transform:uppercase;border-radius:10px;transition:.18s}
.tablabel:hover{color:var(--text);background:rgba(120,170,255,.06)}
.panel{display:none}
#tab-orch:checked~.tabbar label[for=tab-orch],
#tab-agent-0:checked~.tabbar label[for=tab-agent-0],
#tab-agent-1:checked~.tabbar label[for=tab-agent-1],
#tab-agent-2:checked~.tabbar label[for=tab-agent-2]{
  color:#04121a;background:linear-gradient(120deg,var(--c1),var(--c2));
  box-shadow:0 0 18px -3px var(--glow),0 0 28px -8px var(--glow2)}
#tab-orch:checked~.panels #panel-orch,
#tab-agent-0:checked~.panels #panel-agent-0,
#tab-agent-1:checked~.panels #panel-agent-1,
#tab-agent-2:checked~.panels #panel-agent-2{display:block;animation:fade .25s ease}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}

/* logs + chat bubbles */
.log{height:452px;overflow-y:auto;border:1px solid var(--line);border-radius:16px;padding:16px;
  margin:14px 0;background:
    radial-gradient(700px 220px at 50% -10%,rgba(34,211,238,.07),transparent 70%),
    rgba(6,12,26,.45);backdrop-filter:blur(8px)}
.panel .log{height:392px}
.log:empty::before,#orch-log-inner:empty::before{
  content:"// awaiting input — transmit a message below to begin";
  display:block;color:#5e739a;text-align:center;padding:54px 0;font-family:var(--ui);letter-spacing:.6px}
.msg{display:flex;gap:11px;padding:12px 14px;margin:9px 0;border-radius:14px;font-size:13.5px;
  white-space:pre-wrap;max-width:90%;border:1px solid var(--line);
  background:rgba(16,26,48,.55);backdrop-filter:blur(10px);
  box-shadow:0 10px 28px -18px #000,inset 0 1px 0 rgba(255,255,255,.05);animation:pop .2s ease}
@keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.msg::before{content:"";flex:0 0 3px;border-radius:3px;background:var(--line2);align-self:stretch}
.msg-body{flex:1;min-width:0;overflow-wrap:anywhere}
.msg strong{font-weight:700;color:#fff}
.msg span{color:#d6e2f5}
.msg.user{background:linear-gradient(180deg,rgba(34,150,255,.16),rgba(16,26,48,.5));border-color:rgba(56,189,248,.3)}
.msg.user::before{background:var(--c-acc);box-shadow:0 0 12px var(--c-acc)}
.msg.agent{margin-left:auto;background:linear-gradient(180deg,rgba(34,245,197,.14),rgba(16,26,48,.5));border-color:rgba(34,245,197,.3)}
.msg.agent::before{background:#34f5c5;box-shadow:0 0 12px #34f5c5}
.msg.thought{background:linear-gradient(180deg,rgba(168,85,247,.16),rgba(16,26,48,.5));border-color:rgba(168,85,247,.3)}
.msg.thought::before{background:var(--c2);box-shadow:0 0 12px var(--c2)}
.msg.question{background:linear-gradient(180deg,rgba(240,171,252,.16),rgba(16,26,48,.5));border-color:rgba(240,171,252,.35)}
.msg.question::before{background:var(--c3);box-shadow:0 0 12px var(--c3)}
.msg.finish{max-width:100%;background:linear-gradient(180deg,rgba(34,211,238,.14),rgba(168,85,247,.1));
  border-color:rgba(34,211,238,.4);box-shadow:0 0 26px -10px var(--glow)}
.msg.finish::before{background:linear-gradient(180deg,var(--c1),var(--c2));box-shadow:0 0 14px var(--glow)}
.err{color:#ff6e8a;padding:8px 12px;font-size:13px}

/* footer status */
.footer{position:sticky;bottom:0;z-index:5;backdrop-filter:blur(16px);
  background:linear-gradient(0deg,rgba(4,6,12,.92),rgba(4,6,12,.4));
  border-top:1px solid var(--line);padding:11px 6px;margin-top:22px}
.statusline{display:inline-flex;align-items:center;gap:8px;color:var(--muted);
  font-family:var(--ui);font-size:13px;letter-spacing:.6px}
.statusline::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--c1);
  box-shadow:0 0 10px var(--c1);animation:pulse 2s ease-in-out infinite}
"""

# Clear visible text inputs after every WebSocket send (hidden inputs untouched);
# keep all scroll logs pinned to the bottom as new content streams in.
JS = """
document.body.addEventListener('htmx:wsAfterSend', function(e){
  e.detail.elt.querySelectorAll("input[type=text],input:not([type]),textarea").forEach(function(i){i.value='';});
});
function _pin(){document.querySelectorAll('.scroll').forEach(function(e){e.scrollTop=e.scrollHeight;});}
document.body.addEventListener('htmx:wsAfterMessage', _pin);
document.body.addEventListener('htmx:afterSwap', _pin);
"""

app, rt = fast_app(
	pico=False,
	exts='ws',
	hdrs=(Style(CSS), Script(JS)),
	bodykw={'hx_ext': 'ws', 'ws_connect': '/ws'},
)


@rt('/')
def index():
	return _page()


# ── WebSocket: live orchestrator + agent chat ───────────────────────────────
@app.ws('/ws', conn=_on_conn, disconn=_on_disconn)
async def ws(send, action: str = '', goal: str = '', task: str = '', slot: str = '0'):
	if action == 'agent_send':
		s = int(slot)
		t = task.strip()
		if not t:
			return
		await _broadcast(_append(f'chat-{s}-inner', _bubble('user', 'You:', t)))
		asyncio.create_task(_run_agent_send(s, t))

	elif action == 'orch_submit':
		msg = goal.strip()
		if not msg:
			return
		fut = ORCH['ask_future']
		# Answering the orchestrator's pending question
		if fut and not fut.done():
			await _broadcast(_append('orch-log-inner', _bubble('user', '👤 You:', msg)),
			                 _orch_status('Running', 'green'))
			fut.set_result(msg)
			return
		# Injecting a message into a running orchestration
		if ORCH['running']:
			await _broadcast(_append('orch-log-inner', _bubble('user', '👤 You [injected]:', msg)))
			ORCH['injection'] = msg
			return
		# Starting a fresh orchestration
		ORCH.update(running=True, ask_future=None, injection=None)
		await _broadcast(
			_clear('orch-log-inner'),
			_append('orch-log-inner', _bubble('user', '👤 You:', msg)),
			_orch_status('Running', 'green'),
		)
		ORCH['task_obj'] = asyncio.create_task(_run_orch(msg))

	elif action == 'orch_stop':
		fut = ORCH.get('ask_future')
		if fut and not fut.done():
			fut.cancel()
		to = ORCH.get('task_obj')
		if to and not to.done():
			to.cancel()
		ORCH['running'] = False
		await _broadcast(_orch_status('Stopped', 'grey'))


# ── Config ──────────────────────────────────────────────────────────────────
@rt('/config')
def config(api_key: str = '', model: str = '', context: str = ''):
	CONFIG['api_key'] = api_key
	CONFIG['model'] = model or CONFIG['model']
	CONFIG['context'] = context
	set_setting('context', context)
	return _statusline('Settings saved')


# ── Chrome control ──────────────────────────────────────────────────────────
@rt('/chrome/launch-all')
async def chrome_launch_all():
	msg = await asyncio.get_event_loop().run_in_executor(None, launch_chrome)
	return _chrome_badge(msg[:60], 'green'), _statusline(msg)


@rt('/chrome/stop-all')
def chrome_stop_all():
	msg = kill_all_chrome(hard_stop)
	return _chrome_badge('All stopped', 'red'), _statusline(msg)


@rt('/chrome/launch/{s}')
async def chrome_launch(s: int):
	msg = await asyncio.get_event_loop().run_in_executor(None, lambda: launch_slot(s))
	ready = slot_is_ready(s)
	return (_slot_badge(s, f'port {chrome_port(s)}' if ready else 'no Chrome', 'green' if ready else 'red'),
	        _statusline(msg))


@rt('/chrome/kill/{s}')
def chrome_kill(s: int):
	hard_stop(s)
	kill_slot(s)
	return _slot_badge(s, 'stopped', 'red'), _statusline(f'Agent {s + 1} Chrome stopped')


# ── Per-agent control ───────────────────────────────────────────────────────
@rt('/agent/{s}/pause')
def agent_pause(s: int):
	return _statusline(pause_agent(s))


@rt('/agent/{s}/stop')
def agent_stop(s: int):
	return _statusline(stop_agent(s))


@rt('/agent/{s}/reset')
def agent_reset(s: int):
	msg, _ = reset_agent(s)
	return _clear(f'chat-{s}-inner'), _slot_badge(s, 'reset', 'grey'), _statusline(msg)


@rt('/agent/{s}/resume')
async def agent_resume(s: int, instruction: str = ''):
	msg = await resume_agent(s, instruction)
	# clear the inject box
	cleared = Input(name='instruction', id=f'inject-{s}', cls='input grow',
	                placeholder='Inject an instruction while paused…', autocomplete='off',
	                hx_swap_oob='true')
	return cleared, _statusline(msg)


# ── Skills ──────────────────────────────────────────────────────────────────
@rt('/skill/load')
def skill_load(skill: str = ''):
	if not skill:
		return _statusline('Select a skill first')
	return _agent_task_input(0, value=load_skill(skill), oob=True), _statusline(f'Loaded "{skill}"')


@rt('/skill/save')
def skill_save(skill_name: str = '', task: str = ''):
	status, _ = save_skill(skill_name, task)
	return _skill_select(oob=True), _statusline(status)


# ── Reflection ──────────────────────────────────────────────────────────────
@rt('/reflect')
async def reflect():
	try:
		return await reflect_on_runs(CONFIG['api_key'], CONFIG['model'])
	except Exception as e:  # noqa: BLE001
		return f'Error: {e}'


def run(host: str = '127.0.0.1', port: int = 7860) -> None:
	import uvicorn
	uvicorn.run(app, host=host, port=port)
