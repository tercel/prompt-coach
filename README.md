# prompt-dual-coach

A shared Claude Code and Codex `UserPromptSubmit` plugin that coaches:

1. **Prompt quality**: rewrites prompts into clearer coding instructions.
2. **Target language**: identifies expression issues and provides a natural
   rewrite in the language being practiced.

The same analysis core, Hook configuration, and environment variables work in
both agents. Clean prompts and backend failures pass through silently.

## Supported platforms and backends

| Hook platform | Preferred CLI | API fallback |
|---|---|---|
| Claude Code | `claude -p` | Anthropic SDK with `ANTHROPIC_API_KEY` |
| Codex | `codex exec` | OpenAI SDK with `OPENAI_API_KEY` |

`COACH_BACKEND=auto` and `COACH_PLATFORM=auto` are the defaults. During a Hook
run, the plugin detects the platform from its plugin-root environment variable:

- `CLAUDE_PLUGIN_ROOT` selects Claude Code.
- `PLUGIN_ROOT` selects Codex.
- Standalone `--dry-run` defaults to Codex; set `COACH_PLATFORM=claude` to test
  the Claude path.

The selected platform's CLI is tried first. If it fails and the corresponding
API key is available, the plugin falls back to that platform's API.

## Install

### Claude Code

The Claude plugin entry point is `.claude-plugin/plugin.json`:

```text
/plugin marketplace add /absolute/path/to/prompt-dual-coach
/plugin install prompt-dual-coach
```

Restart Claude Code after installation.

#### Claude desktop app (Code mode)

Plugins and hooks run in the desktop app's Code mode too — not just the terminal
CLI. The catch is the hook subprocess's environment: the desktop app is a GUI
process, so its `PATH` is usually a minimal one that does **not** include where
`claude` is installed (e.g. `~/.local/bin`). When `claude` isn't found, the CLI
backend is unavailable and the hook silently no-ops unless an API key is set.

To make it robust regardless of surface, add an `env` block to
`~/.claude/settings.json` — Claude Code injects it into the session and hook
subprocesses, independent of the GUI's `PATH`:

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "COACH_CLAUDE_BIN": "/Users/you/.local/bin/claude"
  }
}
```

- `COACH_CLAUDE_BIN` (absolute path, from `which claude`) keeps the zero-cost CLI
  backend working — it reuses your Claude auth, no API billing.
- `ANTHROPIC_API_KEY` is the fallback so the hook still runs if the CLI binary
  can't be located. Setting both gives CLI-first with API as a safety net.

In the terminal CLI your shell `PATH` already exposes `claude`, so neither is
required there.

### Codex

The Codex plugin entry point is `.codex-plugin/plugin.json`, which points Codex
at the hook via its `"hooks": "./hooks/hooks.json"` field. Add this checkout to a
configured Codex marketplace, install `prompt-dual-coach`, restart Codex, then
review and trust the bundled Hook using `/hooks`.

Both platforms share the same `hooks/hooks.json` and `scripts/coach.py`: Claude
Code discovers the hook through `.claude-plugin/plugin.json`, Codex through the
`hooks` field in `.codex-plugin/plugin.json`.

### Plugin install vs. manual wiring

**Prefer the plugin install above.** The platform auto-detection depends on it:
`COACH_PLATFORM=auto` reads `CLAUDE_PLUGIN_ROOT` / `PLUGIN_ROOT`, and those are
set **only** by the plugin system. Installed as a plugin, Claude Code and Codex
each detect their own platform and pick the right backend automatically, share a
single checkout across both agents, get Codex's `/hooks` trust flow, and stay
consistent across the terminal CLI and the desktop app.

Wire the hook by hand in `~/.claude/settings.json` only when you are actively
editing `scripts/coach.py` and want changes to take effect without reinstalling.
In that case point the `command` at the working copy with an absolute path —
**and set `COACH_PLATFORM=claude` explicitly**, because a manual hook has no
`CLAUDE_PLUGIN_ROOT`, so detection would otherwise fall back to Codex:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/prompt-dual-coach/scripts/coach.py",
            "timeout": 30
          }
        ]
      }
    ]
  },
  "env": { "COACH_PLATFORM": "claude" }
}
```

## Configure

| Variable | Default | Meaning |
|---|---|---|
| `COACH_PLATFORM` | `auto` | `auto`, `claude`, or `codex`. |
| `COACH_BACKEND` | `auto` | `auto`, `cli`, `api`, `claude`, `anthropic`, `codex`, or `openai`. |
| `COACH_CLAUDE_BIN` | PATH lookup | Explicit Claude CLI path. |
| `COACH_CODEX_BIN` | PATH lookup | Explicit Codex CLI path. |
| `ANTHROPIC_API_KEY` | unset | Enables Anthropic API fallback. |
| `OPENAI_API_KEY` | unset | Enables OpenAI API fallback. |
| `COACH_MODEL` | unset | Override every backend model. |
| `COACH_ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude CLI and Anthropic API model. |
| `COACH_CLI_MODEL` | agent default | Codex CLI model. |
| `COACH_API_MODEL` | `gpt-4o-mini` | OpenAI API model. |
| `COACH_TARGET_LANG` | `English` | Language being practiced. |
| `COACH_NATIVE_LANG` | locale detection | Language used for explanations. |
| `COACH_LEVEL` | `Advanced` | Feedback depth. |
| `COACH_MODE` | `annotate` | `annotate` or `block`. |
| `COACH_MIN_PROMPT_CHARS` | `6` | Floor for ultra-short multi-word prompts (see filtering below). |
| `COACH_CONTEXT_MESSAGES` | `6` | Recent transcript turns used as context. |
| `COACH_CONTEXT_CHARS` | `2000` | Maximum rendered context characters. |
| `COACH_TIMEOUT` | `25` | Backend timeout in seconds. |
| `COACH_DISABLE` | unset | Set truthy to disable. |
| `COACH_DEBUG` | unset | Set truthy to print errors. |

`cli` and `api` are platform-aware aliases. Explicit backend names bypass
platform detection:

```bash
COACH_BACKEND=claude    # force Claude CLI
COACH_BACKEND=anthropic # force Anthropic API
COACH_BACKEND=codex     # force Codex CLI
COACH_BACKEND=openai    # force OpenAI API
```

If `COACH_NATIVE_LANG` explicitly equals `COACH_TARGET_LANG`, only prompt
quality coaching runs.

## Which prompts get coached

A cheap deterministic pre-filter runs before any model call. It skips only input
that is unambiguously not worth coaching, so short-but-vague prompts still get
caught:

| Skipped (no model call) | Coached |
|---|---|
| Slash commands, `!shell` | `fix bug`, `review code`, `add tests` |
| Bare answers / flow-control: `yes`, `ok`, `1`, `continue` | `make it better`, `optimize it` |
| Dev command lines: `git push`, `npm install`, `cargo test` | `go implement the login feature` |
| Context-rich phrases: `build it`, `run tests`, `do it` | any ≥2-word natural-language request |
| Single tokens: `refactor`, `optimize` | |

Everything that passes the filter goes to the model, which reads recent
conversation and stays silent on context-clear follow-ups. `make`/`go` are
treated as English words, not CLI commands, so `make it better` is coached.

## Delivery modes

- **`annotate`**: inject coaching as additional developer context, then answer
  the improved request.
- **`block`**: reject the prompt with exit code 2 and require resubmission.

## Try locally

```bash
# Auto defaults to Codex outside a Hook.
python3 scripts/coach.py --dry-run "i want fix login bug when token expire"

# Test Claude auto-selection.
COACH_PLATFORM=claude python3 scripts/coach.py --dry-run "review this prompt"

# Force a specific backend.
COACH_BACKEND=openai python3 scripts/coach.py --dry-run "review this prompt"
COACH_BACKEND=anthropic python3 scripts/coach.py --dry-run "review this prompt"
```

## Develop and test

```bash
python3 tests/test_coach.py -v
```

Unit tests do not call external models.

## Limitations

- Coaching runs after prompt submission, not while typing.
- Annotate mode relies on the active agent following the injected display
  instruction.
- Every non-trivial prompt creates an additional model call.
- Transcript parsing is best-effort because agent transcript formats are not
  stable public APIs.
