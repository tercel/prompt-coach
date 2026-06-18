# prompt-coach

A `UserPromptSubmit` plugin for **Claude Code and Codex** that coaches every
prompt, two ways:

1. **Prompt quality** — rewrites your prompt into a clearer coding instruction,
   plus one teaching tip.
2. **Target language** — corrects your writing in the language you're practicing,
   or translates a native-language prompt into it — each with a short explanation.

One analysis core, hook config, and environment variables across both agents.
Each feature toggles live with the `/prompt-coach:*` commands. Clean prompts and backend
failures pass through silently.

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
/plugin marketplace add /absolute/path/to/prompt-coach
/plugin install prompt-coach
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
configured Codex marketplace, install `prompt-coach`, restart Codex, then
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
            "command": "python3 /absolute/path/to/prompt-coach/scripts/coach.py",
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
| `COACH_BACKEND` | `auto` | `auto`, `cli`, `api`, `ollama`, `claude`, `anthropic`, `codex`, or `openai`. Settable persistently via `/prompt-coach:backend`. |
| `COACH_CLAUDE_BIN` | PATH lookup | Explicit Claude CLI path. |
| `COACH_CODEX_BIN` | PATH lookup | Explicit Codex CLI path. |
| `ANTHROPIC_API_KEY` | unset | Enables Anthropic API fallback. |
| `OPENAI_API_KEY` | unset | Enables OpenAI API fallback. |
| `COACH_MODEL` | unset | Override every backend model. |
| `COACH_ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude CLI and Anthropic API model. |
| `COACH_CLI_MODEL` | agent default | Codex CLI model. |
| `COACH_API_MODEL` | `gpt-4o-mini` | OpenAI API model. |
| `COACH_OLLAMA_HOST` | `http://localhost:11434` | Ollama server base URL (`ollama` backend). |
| `COACH_OLLAMA_MODEL` | `llama3.1` | Ollama model (`ollama` backend). For quality, use a strong instruct model, e.g. `qwen2.5-coder:32b-instruct-q4_K_M`. |
| `COACH_OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model resident between calls (avoids cold-load latency on intermittent hook use). |
| `COACH_TARGET_LANG` | `English` | Language being practiced. |
| `COACH_NATIVE_LANG` | locale detection | Language used for explanations. |
| `COACH_LEVEL` | `Advanced` | Feedback depth. |
| `COACH_EVALUATE` | `off` | Prompt-quality coaching on/off. Overridden live by `/prompt-coach:enable|disable evaluate`. |
| `COACH_CORRECT` | `off` | Target-language correction on/off. Overridden live by `/prompt-coach:enable|disable correct`. |
| `COACH_TRANSLATE` | `off` | Native→target translation on/off. Overridden live by `/prompt-coach:enable|disable translate`. |
| `COACH_STATE_SCOPE` | `project` | `project` or `global` — how widely a `/prompt-coach:*` toggle applies. |
| `COACH_STATE_DIR` | `~/.config/prompt-coach` | Directory for the runtime state file. |
| `COACH_CLI_FLAGS` | unset | Extra space-separated flags for `codex exec` (Codex CLI backend). |
| `COACH_MODE` | `annotate` | `annotate` or `block`. |
| `COACH_MIN_PROMPT_CHARS` | `6` | Floor for ultra-short multi-word prompts (see filtering below). |
| `COACH_CONTEXT_MESSAGES` | `6` | Recent transcript turns used as context. |
| `COACH_CONTEXT_CHARS` | `2000` | Maximum rendered context characters. |
| `COACH_TIMEOUT` | `60` | Backend timeout in seconds. The nested CLI backend can take 15–25s+; too low a value silently drops coaching when a call overruns it. |
| `COACH_DISABLE` | unset | Set truthy to disable. |
| `COACH_DEBUG` | unset | Set truthy to print errors. |

`cli` and `api` are platform-aware aliases. Explicit backend names bypass
platform detection:

```bash
COACH_BACKEND=claude    # force Claude CLI
COACH_BACKEND=anthropic # force Anthropic API
COACH_BACKEND=codex     # force Codex CLI
COACH_BACKEND=openai    # force OpenAI API
COACH_BACKEND=ollama    # force a local Ollama server (COACH_OLLAMA_HOST / COACH_OLLAMA_MODEL)
```

If `COACH_NATIVE_LANG` explicitly equals `COACH_TARGET_LANG`, only prompt
quality coaching runs.

### Dependencies (the API backends are the only ones with extras)

The default paths are **standard-library only** — CLI backends shell out via
`subprocess`, and the `ollama` backend uses `urllib`. No `pip install` required.

The direct HTTP **API** backends (`COACH_BACKEND=api | openai | anthropic`) need
an optional SDK, imported lazily and listed in [`requirements.txt`](requirements.txt):

```bash
pip install anthropic   # Claude / Anthropic API backend
pip install openai      # Codex / OpenAI API backend
```

If you select an API backend without its SDK, `/prompt-coach:status` flags the
missing package instead of failing silently.

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

CJK (Chinese / Japanese / Korean) input is judged by character count, not word
count — those scripts have no spaces, so a whole sentence like `优化这段代码的性能`
is coached, not mistaken for a single token (only 1-character replies like `好` are
skipped).

## Commands

Claude Code namespaces plugin commands as `/<plugin>:<command>`, so each action is
its own command (type the verb in the `/` menu to fuzzy-match). They run at runtime
with no restart — taking effect on your next prompt:

| Command | Effect |
|---|---|
| `/prompt-coach:power on` · `… off` | The **entire** hook on/off (feature states preserved) |
| `/prompt-coach:enable <feature…>` | Turn one or more features **on** |
| `/prompt-coach:disable <feature…>` | Turn one or more features **off** |
| `/prompt-coach:lang native <X> target <Y>` | Set your native / practiced language (name or code, e.g. `native zh target en`) |
| `/prompt-coach:backend <auto\|cli\|api\|ollama> [model]` | Choose the analysis engine (auto=CLI default; api/ollama are faster, more reliable). For `ollama`, pass a pulled model, e.g. `backend ollama qwen2.5-coder:32b-instruct-q4_K_M` — it persists, no global config needed |
| `/prompt-coach:status` | Show current state (each feature, scope, state-file path) |
| `/prompt-coach:help [en\|zh]` | Show the command usage (English or Chinese) |

**Features** (use the full name or its single letter `e` · `c` · `t`):
`evaluate` (prompt-quality coaching) · `correct` (fix your *target-language*
writing) · `translate` (render *native-language* input in the target language).
So `/prompt-coach:enable c t` == `… enable correct translate`. `/prompt-coach:help
zh` shows the usage in Chinese (`en` default). Set your languages with
`/prompt-coach:lang native zh target en` (full name or code; persists, overrides
`COACH_NATIVE_LANG` / `COACH_TARGET_LANG`).

You can pass several at once: `/prompt-coach:enable correct translate` (= auto:
correct what you write in the target language, translate what you write in your
native one); `/prompt-coach:disable correct translate` turns all language coaching
off. Separators are flexible — space, comma, or hyphen (`disable correct,translate`).

**Opt-in by default: all features start OFF** — a fresh install does nothing (and
when everything is off the hook exits before any model call). Turn on what you want:
a Chinese speaker practicing English might `/prompt-coach:enable correct translate`
(correct your English, translate your Chinese), or just `/prompt-coach:enable evaluate`
for prompt-quality tips only. Set this per project with `.claude/settings.local.json`
for Claude or `.codex/config.toml` for Codex so each project opts in
independently.

### Codex

Codex reads custom prompts from `$CODEX_HOME/prompts/` (flat namespace), and a
prompt runs without `PLUGIN_ROOT`, so the commands can't be plain-symlinked like a
Claude hook. Generate them once (bakes in coach.py's absolute path):

```bash
bash scripts/install-codex-prompts.sh   # writes ~/.codex/prompts/prompt-coach-*.md
```

Then invoke in Codex with the same verbs, `$`-prefixed and hyphen-namespaced:

| Claude | Codex |
|---|---|
| `/prompt-coach:power on` | `$prompt-coach-power on` |
| `/prompt-coach:enable correct translate` | `$prompt-coach-enable correct translate` |
| `/prompt-coach:disable evaluate` | `$prompt-coach-disable evaluate` |
| `/prompt-coach:status` | `$prompt-coach-status` |
| `/prompt-coach:help` | `$prompt-coach-help` |

Re-run the script after moving the repo. (The same Codex format — YAML frontmatter
+ `$ARGUMENTS` — is why these work; `agent-skill-bundler` only converts *skills*,
not hook commands, so it isn't involved here.)

Each toggle is written to a small state file under `~/.config/prompt-coach/`
(`state.json`, or `state.<projecthash>.json` under project scope; override the dir
with `COACH_STATE_DIR`; never inside your project) that the hook
reads every prompt; it overrides the `COACH_EVALUATE` / `COACH_CORRECT` /
`COACH_TRANSLATE` / `COACH_DISABLE` env defaults. The path is intentionally a fixed
home location, **not** `CLAUDE_PLUGIN_DATA` / `PLUGIN_DATA` — those variables are
set for the hook but not for the control command subprocess, so keying off them
would make the command and the hook read different files (your toggles would
silently never apply).

### Where settings live: global config vs per-project state

prompt-coach keeps two files under `~/.config/prompt-coach/` (override the dir with
`COACH_STATE_DIR`):

| File | Holds | Written by | Scope |
|---|---|---|---|
| `config.json` | backend, Ollama model/host/keep-alive, native/target language — **plus the feature toggles when scope is `global`** | `/prompt-coach:backend`, `/prompt-coach:lang`; `/prompt-coach:enable`/`disable`/`power` (in global scope) | **Global, cross-platform** — one file both Claude and Codex read |
| `state.<project>.<hash>.json` | `evaluate` / `correct` / `translate` toggles, power | `/prompt-coach:enable` / `disable` / `power` (in project scope) | **Per-project** (the default scope) |

There is **one global file** (`config.json`), not two: in `global` scope the feature
toggles live in `config.json` alongside backend/language, so there is no separate
`state.json`. Only **project** scope adds the per-project `state.<project>.<hash>.json`
files — because a single global file can't hold per-project toggles.

Why a dedicated `config.json` instead of host settings: `~/.claude/settings.json`
is Claude-only and `~/.codex/config.toml` is Codex-only — neither is shared. A file
in the prompt-coach home dir is the one place both hooks read, so your backend and
language are set once and apply everywhere.

Resolution: per-project state `>` `config.json` `>` env (`COACH_*`) `>` built-in
default. Feature toggles live in the per-project state file (project scope) **or in
`config.json` (global scope)** — they are not inherited across scopes.

#### File templates and schema

Both files are created on demand by the `/prompt-coach:*` commands — **you never
need to create them**, and a fresh install with neither file just uses built-in
defaults (everything off). The repo ships [`config.example.json`](config.example.json)
(templates the global `config.json`) and
[`state.project.example.json`](state.project.example.json) (templates a per-project
`state.<project>.<hash>.json`) for reference; to hand-edit, copy a template (JSON has
no comments, so the keys are documented below):

```bash
mkdir -p ~/.config/prompt-coach
cp config.example.json ~/.config/prompt-coach/config.json   # then edit
```

`config.json` — the single global, cross-platform file (every key optional; omit to
use the default):

| Key | Values | Default |
|---|---|---|
| `backend` | `auto` · `cli` · `api` · `ollama` · `codex` · `openai` · `claude` · `anthropic` | `auto` |
| `ollama_model` | any pulled Ollama model tag | `llama3.1` |
| `ollama_host` | Ollama base URL | `http://localhost:11434` |
| `ollama_keep_alive` | Ollama keep-alive duration (e.g. `30m`, `2h`, `-1` = forever) | `30m` |
| `native` | your native language (name or code) | locale-detected |
| `target` | language you're practicing | `English` |
| `enabled` / `evaluate` / `correct` / `translate` | **global scope only** — the feature toggles below, when `COACH_STATE_SCOPE=global` | see state table |

`state.<project>.<hash>.json` — per-project feature toggles (the filename is
auto-derived; `project` is written automatically so `cat` tells you which path it
belongs to). Only created in the default **project** scope:

| Key | Values | Default |
|---|---|---|
| `enabled` | `true` / `false` — the master power switch | `true` (absent ⇒ on) |
| `evaluate` | `true` / `false` — prompt-quality coaching | `false` |
| `correct` | `true` / `false` — correct target-language writing | `false` |
| `translate` | `true` / `false` — render native input in the target | `false` |
| `project` | absolute project path (auto-written) | — |

### Toggle scope (`COACH_STATE_SCOPE`)

How widely a `/prompt-coach:*` **feature** toggle reaches (backend/lang are always
global — see above):

| Scope | Behavior |
|---|---|
| `project` *(default)* | Isolated per `CLAUDE_PROJECT_DIR` — "translate in project A" leaves project B untouched. Falls back to the shared file when no project dir can be resolved. |
| `global` | One shared switch — a toggle affects every session and project. |

**Per-session scope is not offered.** The platform exposes `session_id` only in the
hook's stdin payload, not as an env var, so the `/prompt-coach:*` commands (a plain subprocess)
can't know which session it's in. `project` is the finest reliable granularity; `/prompt-coach:status` prints the active scope and the exact state-file path.

### Per-project opt-in (recommended)

Because everything is **off by default**, the cleanest setup is to enable coaching
only in the projects where you want it.

For Claude Code, use that project's `.claude/settings.local.json` (personal,
gitignored):

```json
{
  "env": {
    "COACH_NATIVE_LANG": "Chinese",
    "COACH_TARGET_LANG": "English",
    "COACH_CORRECT": "on",
    "COACH_TRANSLATE": "on"
  }
}
```

For Codex, use `.codex/config.toml` in a trusted project:

```toml
[shell_environment_policy]
set = {
  COACH_NATIVE_LANG = "Chinese",
  COACH_TARGET_LANG = "English",
  COACH_CORRECT = "on",
  COACH_TRANSLATE = "on",
}
```

- Project settings **override the global defaults**, so this works even with no
  global config — a project sets what it wants; built-in default remains off.
- Codex only loads project-local `.codex/config.toml` after the project is
  trusted. Project-local Codex config can also define hooks, but don't add a
  second prompt-coach hook there if the plugin hook is already installed.
- Projects without these files get nothing (default off), and the hook exits
  before any model call, so it's zero-cost there.
- This env/config approach is inherently per-project, so you don't need
  `COACH_STATE_SCOPE` or the `/prompt-coach:*` commands here — use the commands
  instead when you want to toggle a feature **live** within a session.

A global `~/.claude/settings.json` `env` is optional — put machine-wide defaults
there (e.g. `COACH_NATIVE_LANG`), and any project can still override them. Codex
machine-wide defaults can live in `~/.codex/config.toml` under
`[shell_environment_policy].set`.

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

# Translate mode: write in your native language, get a target-language version.
COACH_CORRECT=off COACH_TRANSLATE=on COACH_NATIVE_LANG=Chinese \
  python3 scripts/coach.py --dry-run "帮我修复登录时 token 过期的 bug"
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
