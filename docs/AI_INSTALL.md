# Install with an AI agent

Paste this prompt into **Claude Code, Codex CLI, Cursor, Gemini CLI, Hermes Agent** or any
coding agent that can run shell commands:

```text
Install the Grok + JEV research desk for me by following
https://github.com/achupradeep3050/grok-jev-research-desk/blob/main/docs/AI_INSTALL.md
exactly. Ask me for any key or setting you need, never print my secrets, keep execution in
paper mode, and finish by showing me the real output of `jevdesk doctor`.
```

---

## Instructions for the agent

Follow these steps exactly. Stop and ask the user whenever a step needs a decision or a secret.

### Rules

- **Never print, log, commit or echo a secret** (xAI key, Telegram token, webhook secret).
  Write secrets only into `.env` (mode 600). When you show configuration, show `set` / `not set`.
- **Keep `EXECUTION_MODE=paper`** unless the user explicitly asks otherwise in this
  conversation and supplies their own webhook URL and secret.
- Do not edit the risk logic (`jevdesk/approval.py`, `jevdesk/jev_desk.py`) during an install.
- Report real command output. If a step fails, say so and show the error.

### 1. Install

```bash
git clone https://github.com/achupradeep3050/grok-jev-research-desk.git
cd grok-jev-research-desk
bash install.sh --no-prompt
```

Expected: `14/14 passed` from the self-test, all unit tests pass, and `jevdesk doctor` lists
GOLD, BTC and GBPUSD as `OK`. At this point `doctor` may end with `PROBLEMS FOUND` only
because the LLM or Telegram is not configured yet — that is fine.

### 2. Configure (ask the user, one question at a time)

1. **LLM** — an xAI API key (`XAI_API_KEY`, from https://console.x.ai), or a local
   OpenAI-compatible server (`LLM_BASE_URL` + `LLM_MODEL`, e.g. Ollama). Optional.
2. **Telegram** — a bot token from @BotFather (`TELEGRAM_BOT_TOKEN`) and the user's numeric
   Telegram id (`TELEGRAM_OPERATOR_IDS`; @userinfobot shows it). Optional; without it the
   desk only logs cards to the console.
3. **Risk** — USD risked per trade (`RISK_PER_TRADE_USD`, default 100) and the flat
   dead zone (`DEAD_ZONE_UTC`, default `20-24`, or `off`).

Write each value into `.env` by replacing its `KEY=` line. Keep the file mode 600.

### 3. Verify

```bash
.venv/bin/jevdesk doctor             # must end with: RESULT: HEALTHY
.venv/bin/jevdesk eval gold          # deterministic pipeline, prints JSON
.venv/bin/jevdesk eval gold --llm    # only with an LLM: engine must be "grok"
```

If Telegram is configured, run `.venv/bin/jevdesk run` in the foreground for a minute,
confirm the user received "research desk online", ask them to send `/status` and confirm
the reply. Stop it with Ctrl-C.

### 4. Background service (only if the user wants it)

```bash
bash install.sh --no-prompt --service   # launchd (macOS) or systemd --user (Linux)
# or
docker compose up -d --build
```

### 5. Report

Tell the user where it is installed, which feeds are OK, whether the LLM and Telegram checks
passed, the risk settings in force, that execution is **paper**, and how to stop the service.
