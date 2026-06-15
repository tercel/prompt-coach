# prompt-dual-coach

A Claude Code plugin (a `UserPromptSubmit` hook) that coaches **two axes on every
prompt you submit**:

1. **Prompt quality** — rewrites your prompt into a clearer instruction for a
   coding assistant, plus one teaching tip (missing file paths, success
   criteria, constraints…).
2. **Target language** — corrects your expression in your chosen target language
   (not just English): concrete `original → fix (why)` corrections **and** a
   fully rewritten, natural version.

It is a **prototype of the "brain"** for a future real-time product (terminal
split-pane / floating panel). The analysis logic here (`scripts/coach.py`) is the
reusable core; only the delivery shell changes later.

> Why this exists: existing tools tend to do one or the other — fix your
> language, or improve your prompt. This fuses both at submit time, and validates
> that combined loop cheaply.

## Requirements

- Claude Code (recent version with plugin + hooks support)
- Python 3.8+
- A backend (pick one — **CLI is the zero-config default**):
  - **CLI (default):** the `claude` command on your PATH. No pip install, no
    separate API key — it reuses your Claude Code auth.
  - **API:** `pip install anthropic` + `ANTHROPIC_API_KEY`.

With no usable backend the hook **no-ops silently** — it never blocks your
workflow.

## Backends

Set `COACH_BACKEND`:

| Value | Behavior |
|---|---|
| `auto` (default) | Use the `claude` CLI if found, else fall back to the Anthropic SDK + API key. |
| `cli` | Force the `claude` CLI (`claude -p --output-format json`). |
| `api` | Force the Anthropic Python SDK (needs `pip install anthropic` + `ANTHROPIC_API_KEY`). |

The CLI backend spawns `claude -p`, which itself fires `UserPromptSubmit`. To
avoid infinite recursion the hook sets `COACH_NESTED=1` on the child process, and
the nested invocation exits immediately. (The CLI path has no JSON-schema
enforcement, so output is parsed tolerantly — fences/preamble are stripped; the
API path additionally enforces the schema via `output_config.format`.)

## Install

From a local checkout (adjust the path to wherever you cloned this):

```
/plugin marketplace add /absolute/path/to/prompt-dual-coach
/plugin install prompt-dual-coach
```

Restart Claude Code so the hook registers. (Plugin packaging may need a small
tweak to match your Claude Code version's marketplace format — the load-bearing
file is `scripts/coach.py`; you can also wire it directly in `settings.json`, see
below.)

### Or wire it manually (no marketplace)

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python3 /absolute/path/to/prompt-dual-coach/scripts/coach.py", "timeout": 30 }
        ]
      }
    ]
  }
}
```

## Configure

All via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `COACH_BACKEND` | `auto` | `auto` \| `cli` \| `api` (see Backends). |
| `COACH_CLAUDE_BIN` | (PATH) | Path to the `claude` binary if not on PATH. |
| `ANTHROPIC_API_KEY` | — | Required only for the `api` backend / fallback. |
| `COACH_TARGET_LANG` | `English` | The language you want to improve (any language). |
| `COACH_NATIVE_LANG` | auto | Your native language (explanations are written in it). Auto-detected from your locale (`LANG`/`LC_*`); fallback `English`. Set to override. |

> If you **explicitly** set `COACH_NATIVE_LANG` equal to `COACH_TARGET_LANG`
> (a native practicing their own language), the language axis is turned off and
> you get prompt-quality coaching only. If they only match because native was
> *auto-detected* (e.g. an English-locale machine), the language axis stays on —
> for a true native it simply finds nothing to fix.
| `COACH_LEVEL` | `Advanced` | Free text; tunes feedback depth. Recommended: `Beginner` \| `Intermediate` \| `Advanced` (or CEFR `A1`–`C2`). |
| `COACH_MODEL` | `claude-haiku-4-5` | Fast/cheap by design; runs on every prompt. |
| `COACH_MODE` | `annotate` | `annotate` (non-blocking) or `block` (see below). |
| `COACH_MIN_PROMPT_CHARS` | `12` | Skip prompts shorter than this. |
| `COACH_CONTEXT_MESSAGES` | `6` | Recent conversation turns fed to the model for context. `0` = isolate. |
| `COACH_CONTEXT_CHARS` | `2000` | Max characters of rendered context. |
| `COACH_TIMEOUT` | `25` | Backend timeout in seconds (hook timeout is 30). |
| `COACH_DISABLE` | — | Set truthy to disable without uninstalling. |
| `COACH_DEBUG` | — | Set truthy to print errors to stderr. |

Example (improving English, default CLI backend — no key needed):

```
export COACH_TARGET_LANG=English
# COACH_NATIVE_LANG is auto-detected from your locale (e.g. LANG=zh_CN.UTF-8 -> Chinese);
#   set it only to override.
export COACH_LEVEL=Advanced      # or Beginner / Intermediate, or CEFR A1-C2
# COACH_BACKEND defaults to auto -> uses the `claude` CLI if present
```

## Two delivery modes

A `UserPromptSubmit` hook has **no direct stdout→user channel**, so coaching is
delivered one of two ways:

- **`annotate` (default, non-blocking):** injects the coaching as
  `additionalContext` instructing Claude to show the block at the top of its
  reply and answer the *improved* prompt. You keep working; you see coaching +
  still get your answer.
- **`block` (strict learning loop):** surfaces the coaching and **blocks** the
  prompt (exit 2) so you consciously resubmit the improved version. More
  friction, stronger learning.

Clean prompts (no issues on either axis) pass through silently.

## How it works

```
UserPromptSubmit hook (coach.py)
  -> COACH_NESTED set? exit (recursion guard for the CLI backend)
  -> read the submitted prompt + transcript_path
  -> pre-filter (skip slash commands / very short input)
  -> read recent turns from the session transcript (conversation context)
  -> one fast Claude call (CLI `claude -p`, or Anthropic SDK) -> JSON:
       {language:{corrections, improved}, prompt:{improved, guidance}}
  -> render a dual-axis coaching block
  -> annotate (inject context) OR block (exit 2)
```

**Context-aware.** The hook reads the last few turns from the session's
transcript (`transcript_path`) and judges your new prompt *in context* — so a
terse follow-up that is clear given the conversation is not flagged as "vague".
The language axis still evaluates the new prompt's expression. Set
`COACH_CONTEXT_MESSAGES=0` to analyze each prompt in isolation.

The API backend uses the Messages API with `output_config.format` (JSON schema)
for guaranteed-valid JSON; the CLI backend parses the model's JSON tolerantly.

## Try it locally (no hook wiring)

See the actual coaching output against any prompt, using your configured backend:

```
python3 scripts/coach.py --dry-run "i want fix the login bug, it not work when token expire"
# or pipe it:
echo "now do the same for the logout flow" | python3 scripts/coach.py --dry-run
```

It prints the dual-axis coaching block (or "looks good" if there's nothing to
fix). This is the fastest way to judge the feedback quality before wiring the
hook into Claude Code. (Dry-run analyzes the prompt without conversation
context.)

## Develop / test

Pure helpers are unit-tested with no network or SDK dependency:

```
python3 tests/test_coach.py -v
```

18 tests cover pre-filtering, JSON parsing, coaching formatting, and both
delivery modes.

## Limitations (honest)

- **Post-submit, not as-you-type.** Hooks fire after you press Enter. True
  live "while typing" feedback needs a different shell (terminal compose pane /
  PTY wrapper / floating panel) — that's the next phase. This brain is portable
  to all of them.
- **Annotate mode relies on Claude rendering the block.** Usually fine; `block`
  mode is deterministic if you want guaranteed visibility.
- **A model call per non-trivial prompt** costs a little latency + tokens
  (Haiku keeps it cheap). Pre-filters and the "silent when clean" path keep it
  out of the way.
- This is a **prototype to validate the feedback**, not the final product.
