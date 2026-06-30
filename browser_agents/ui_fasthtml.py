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
from .runs import reflect_on_runs, distill_lessons, load_learned
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
			Div(
				Button('Reflect on recent runs', cls='btn btn-ghost',
				       hx_post='/reflect', hx_target='#reflect-out', hx_swap='innerHTML',
				       hx_disabled_elt='this'),
				Button('Auto-improve prompts', cls='btn btn-ghost',
				       hx_post='/improve', hx_target='#reflect-out', hx_swap='innerHTML',
				       hx_disabled_elt='this'),
				cls='row gap',
			),
			Pre(load_learned(), id='reflect-out', cls='reflect'),
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
:root{
  --bg:#0a0c10; --panel:#13171f; --panel2:#0c0f15; --border:#222936;
  --border2:#2e3848; --text:#e8eef5; --muted:#8a94a6; --accent:#7c83ff;
  --accent2:#6366f1; --glow:rgba(124,131,255,.35);
}
*{box-sizing:border-box}
body{margin:0;background:
  radial-gradient(1100px 520px at 78% -8%,rgba(124,131,255,.10),transparent 60%),
  radial-gradient(900px 480px at 0% 0%,rgba(168,85,247,.07),transparent 55%),
  var(--bg);
  color:var(--text);min-height:100vh;
  font:14px/1.55 'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.app{max-width:1200px;margin:0 auto;padding:0 20px 48px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#2a3340;border-radius:8px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:#384458;background-clip:padding-box}

/* topbar */
.topbar{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:12px;
  padding:16px 6px;margin-bottom:6px;backdrop-filter:blur(10px);
  background:linear-gradient(180deg,rgba(10,12,16,.92),rgba(10,12,16,.72));
  border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:10px;font-size:21px}
.brand>span:first-child,.brand :first-child{font-size:22px}
.brand-name{font-weight:800;letter-spacing:-.2px;
  background:linear-gradient(90deg,#fff,#c7cbff);-webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent}
.grow{flex:1}

/* badges */
.badge{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;border-radius:999px;
  font-size:12px;font-weight:600;border:1px solid var(--border2);white-space:nowrap}
.badge::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;
  box-shadow:0 0 8px currentColor}
.badge.grey{background:#1a2029;color:#9aa4b2}
.badge.green{background:rgba(34,197,94,.12);color:#4ade80;border-color:rgba(34,197,94,.3)}
.badge.red{background:rgba(248,81,73,.12);color:#ff6b63;border-color:rgba(248,81,73,.3)}
.badge.orange{background:rgba(227,160,8,.12);color:#fbbf24;border-color:rgba(227,160,8,.3)}
.badge.blue{background:rgba(88,166,255,.12);color:#6cb2ff;border-color:rgba(88,166,255,.3)}

/* buttons */
.btn{appearance:none;border:1px solid var(--border2);background:#1b212c;color:var(--text);
  padding:9px 15px;border-radius:9px;font-weight:600;cursor:pointer;font-size:13px;
  transition:transform .08s ease,background .15s,border-color .15s,box-shadow .15s}
.btn:hover{border-color:#3c4860;background:#222a37}
.btn:active{transform:translateY(1px)}
.btn-sm{padding:6px 11px;font-size:12px;border-radius:8px}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent2));border:none;color:#fff;
  box-shadow:0 4px 14px -4px var(--glow)}
.btn-primary:hover{box-shadow:0 6px 20px -4px var(--glow);filter:brightness(1.07)}
.btn-ghost{background:transparent}
.btn-danger{background:transparent;color:#ff6b63;border-color:rgba(248,81,73,.35)}
.btn-danger:hover{background:rgba(248,81,73,.1)}
.btn.htmx-request,.btn:disabled{opacity:.55;pointer-events:none}

/* layout helpers */
.row{display:flex}.gap{gap:8px}.items-center{align-items:center}
.stack{display:flex;flex-direction:column;gap:10px}

/* cards */
.control-row{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:16px 0}
.card{position:relative;background:linear-gradient(180deg,var(--panel),#10141b);
  border:1px solid var(--border);border-radius:16px;padding:18px;
  box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 10px 30px -18px rgba(0,0,0,.8);
  transition:border-color .15s,transform .15s}
.control-row .card{display:flex;flex-direction:column}
.control-row .card:hover{border-color:var(--border2);transform:translateY(-2px)}
.card-title{display:flex;align-items:center;gap:8px;font-weight:700;font-size:11.5px;
  text-transform:uppercase;letter-spacing:.9px;color:var(--muted);margin-bottom:13px}
.card-title::before{content:"";width:4px;height:14px;border-radius:3px;
  background:linear-gradient(180deg,var(--accent),var(--accent2))}

/* inputs */
.input{width:100%;background:var(--panel2);color:var(--text);border:1px solid var(--border2);
  border-radius:9px;padding:10px 12px;font:inherit;font-size:13px;outline:none;transition:.15s}
.input::placeholder{color:#5d6878}
.input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--glow)}
textarea.input{resize:vertical}
.composer{align-items:flex-end}

.reflect{margin:13px 0 0;flex:1;min-height:90px;max-height:200px;overflow:auto;background:var(--panel2);
  border:1px solid var(--border2);border-radius:10px;padding:12px;white-space:pre-wrap;
  font-size:12.5px;color:var(--text)}
.reflect:empty::before{content:"Suggestions from your recent runs will appear here.";color:#5d6878}

/* tabs (pure CSS) */
.tabs{margin-top:8px}
.tabradio{display:none}
.tabbar{display:flex;gap:6px;margin-bottom:16px;padding:5px;border-radius:12px;
  background:var(--panel2);border:1px solid var(--border)}
.tablabel{padding:9px 18px;cursor:pointer;color:var(--muted);font-weight:600;font-size:13px;
  border-radius:9px;transition:.15s}
.tablabel:hover{color:var(--text);background:rgba(255,255,255,.03)}
.panel{display:none}
#tab-orch:checked~.tabbar label[for=tab-orch],
#tab-agent-0:checked~.tabbar label[for=tab-agent-0],
#tab-agent-1:checked~.tabbar label[for=tab-agent-1],
#tab-agent-2:checked~.tabbar label[for=tab-agent-2]{
  color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2));
  box-shadow:0 4px 14px -5px var(--glow)}
#tab-orch:checked~.panels #panel-orch,
#tab-agent-0:checked~.panels #panel-agent-0,
#tab-agent-1:checked~.panels #panel-agent-1,
#tab-agent-2:checked~.panels #panel-agent-2{display:block}

/* logs + chat bubbles */
.log{height:452px;overflow-y:auto;background:
  radial-gradient(600px 200px at 50% -10%,rgba(124,131,255,.05),transparent 70%),var(--panel2);
  border:1px solid var(--border);border-radius:14px;padding:14px;margin:14px 0}
.panel .log{height:392px}
.log:empty::before,#orch-log-inner:empty::before{content:"No activity yet — send a message below to begin.";
  display:block;color:#5d6878;text-align:center;padding:48px 0;font-size:13px}
.msg{display:flex;gap:10px;padding:11px 13px;margin:8px 0;border-radius:12px;font-size:13px;
  white-space:pre-wrap;background:#161d28;border:1px solid var(--border);
  box-shadow:0 4px 14px -10px rgba(0,0,0,.7);max-width:94%;animation:pop .18s ease}
@keyframes pop{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.msg::before{content:"";flex:0 0 4px;border-radius:3px;background:var(--border2);align-self:stretch;margin:-1px 0}
.msg-body{flex:1;min-width:0;overflow-wrap:anywhere}
.msg strong{font-weight:700;color:#fff}
.msg span{color:#cdd5e0}
.msg.user{background:linear-gradient(180deg,#15233a,#121b2c)}
.msg.user::before{background:#3b82f6;box-shadow:0 0 10px rgba(59,130,246,.5)}
.msg.agent{background:linear-gradient(180deg,#10271c,#0d2018);margin-left:auto}
.msg.agent::before{background:#22c55e;box-shadow:0 0 10px rgba(34,197,94,.5)}
.msg.thought{background:linear-gradient(180deg,#181736,#13122a)}
.msg.thought::before{background:#818cf8;box-shadow:0 0 10px rgba(129,140,248,.5)}
.msg.question{background:linear-gradient(180deg,#221541,#1a1033)}
.msg.question::before{background:#a78bfa;box-shadow:0 0 10px rgba(167,139,250,.5)}
.msg.finish{background:linear-gradient(180deg,#2a230f,#201b0b);max-width:100%}
.msg.finish::before{background:#f59e0b;box-shadow:0 0 10px rgba(245,158,11,.5)}
.err{color:#ff6b63;padding:8px 12px;font-size:13px}

/* footer status */
.footer{position:sticky;bottom:0;backdrop-filter:blur(10px);
  background:linear-gradient(0deg,rgba(10,12,16,.92),rgba(10,12,16,.6));
  border-top:1px solid var(--border);padding:10px 6px;margin-top:20px}
.statusline{display:inline-flex;align-items:center;gap:7px;color:var(--muted);font-size:12.5px}
.statusline::before{content:"";width:6px;height:6px;border-radius:50%;
  background:#4ade80;box-shadow:0 0 8px #4ade80}
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


@rt('/improve')
async def improve():
	"""Distill recent runs into the persisted standing instructions and show them.
	Future agents pick these up automatically via extend_system_message."""
	try:
		return await distill_lessons(CONFIG['api_key'], CONFIG['model']) or 'No runs to learn from yet.'
	except Exception as e:  # noqa: BLE001
		return f'Error: {e}'


def run(host: str = '127.0.0.1', port: int = 7860) -> None:
	import uvicorn
	uvicorn.run(app, host=host, port=port)
