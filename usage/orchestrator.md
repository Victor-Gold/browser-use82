# Orchestrator Mode

## What it is

The Orchestrator is an LLM agent that treats browser agents as tools. Instead of you deciding which agent does what, the orchestrator LLM figures that out autonomously.

You give it a high-level goal. It thinks, dispatches agents, reads their results, thinks again, dispatches more agents if needed, and eventually produces a final synthesized answer.

## How to use it

1. Click the **🧠 Orchestrator** tab
2. Type a high-level goal (e.g. *"Research the top 5 crypto projects launching this month and summarize their tokenomics"*)
3. Click **Orchestrate**
4. Watch the **Reasoning log** — every dispatch and return is shown in real time
5. Final answer appears in the **Final answer** section when the orchestrator calls finish

## What happens behind the scenes

```
You → high-level goal
        ↓
  Orchestrator LLM
  thinks: "I need agent 1 to search X"
        ↓
  → Agent 1 dispatched (runs real Chrome)
  ← Agent 1 returns result
        ↓
  Orchestrator LLM
  thinks: "I need agents 2+3 in parallel for Y and Z"
        ↓
  → Agent 2 + Agent 3 dispatched simultaneously
  ← Both return results
        ↓
  Orchestrator LLM
  thinks: "I have enough, synthesizing"
        ↓
  Final answer
```

The orchestrator can call agents one at a time or in parallel (`call_agents_parallel`). It decides based on whether the sub-tasks are independent.

## Admin vs orchestrator dispatch

Both paths use the same underlying `pool.send_task`:

- **You (admin)** → use the Agent 1/2/3 tabs directly, full control
- **Orchestrator LLM** → dispatches via the Orchestrator tab, autonomous

There's no conflict — if the orchestrator is using Agent 1 and you send a task to Agent 2 from its tab, they run concurrently.

## Limits

- Max 20 orchestrator reasoning steps (hard cap in `orchestrator.py:MAX_STEPS`)
- The orchestrator uses the same model as selected in the Config card
- Agent results that feed back to the orchestrator are truncated if very long — the orchestrator sees the full chat response
