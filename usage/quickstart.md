# Quickstart

## Run the UI

```
& c:/SICA/browser-use82/.venv/Scripts/python.exe c:/SICA/browser-use82/gemma_ui.py
```

Open `http://127.0.0.1:7860` in your browser.

## First run checklist

1. **API Key** — paste your Google AI API key in the Config card (or put `GOOGLE_API_KEY=...` in `.env`)
2. **Model** — default is `gemma-4-31b-it`. Gemma is free but flaky; the agent retries up to 50 failures automatically
3. **Chrome** — click **Launch All Chrome** in the header to pre-start all 3 browsers, or just send a task and the correct Chrome auto-launches for that slot
4. **Send a task** — go to the Agent 1 tab, type a task, hit Send or Enter

## Sending tasks

- Each agent tab is fully independent — Agent 1, 2, 3 each control their own Chrome window
- After an agent finishes, send another task in the same tab — the agent continues from where it left off (same browser session, same history)
- To start completely fresh, click **↺ Reset** which clears the agent and chat log

## Pause / inject / resume

1. Click **⏸** to pause the agent mid-task
2. Type a correction or new instruction in the **Inject instruction** box
3. Click **▶ Resume** — the injected instruction is added to the agent's context before it continues

## Results

Every completed task writes a markdown file to `results/` with the task, timestamp, and full agent response. The last 50 runs are also in `runs_log.json`.
