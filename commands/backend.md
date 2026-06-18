---
description: prompt-coach — choose the analysis backend (auto | cli | api | ollama [model])
argument-hint: "[auto|cli|api|ollama] [model]"
---

Run EXACTLY this Bash command and show its stdout verbatim — do nothing else:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/coach.py" --ctl backend $ARGUMENTS
```

`$ARGUMENTS` selects the engine that analyzes your prompts:

- `auto` (default) — use the hook platform's CLI, then its API. Robust but slow:
  it spawns a nested agent (often 15-25s+), so it can overrun `COACH_TIMEOUT` and
  silently drop coaching.
- `cli` — force the platform CLI (same nested-agent cost as auto).
- `api` — call the platform's HTTP API directly (fast, reliable). Needs
  `OPENAI_API_KEY` (Codex/OpenAI) or `ANTHROPIC_API_KEY` (Claude/Anthropic).
- `ollama` — call a local Ollama server (`COACH_OLLAMA_HOST`, default
  `http://localhost:11434`). Pass a **pulled** model as the second argument so you
  don't have to touch global config, e.g.
  `backend ollama qwen2.5-coder:32b-instruct-q4_K_M`. Without it, the model falls
  back to `COACH_OLLAMA_MODEL` / `llama3.1`; if that model isn't pulled the call
  fails and the hook stays silent. `/prompt-coach:status` shows the active model.

The backend (and the Ollama model, when given) are saved to the cross-platform
global config (`~/.config/prompt-coach/config.json`), overriding `COACH_BACKEND` /
`COACH_OLLAMA_MODEL`. It applies to every project and to both Claude and Codex
(unlike `~/.claude/settings.json`, which Codex can't read). Feature toggles stay
per-project. Takes effect on your next prompt — no restart.
