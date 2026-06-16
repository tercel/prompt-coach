---
description: Control prompt-coach — power switch + enable/disable coaching features
argument-hint: "[power on|power off|enable <feature…>|disable <feature…>|status|help]"
---

Run EXACTLY this Bash command and show the user its stdout verbatim — do nothing else:

```bash
python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/scripts/coach.py" --ctl $ARGUMENTS
```

`$ARGUMENTS` (default `status` if empty):

- `power on` / `power off` — the whole coaching hook
- `enable <feature …>` — turn one or more features on
- `disable <feature …>` — turn one or more features off
- `status` — show current state (each feature, scope, state-file path)
- `help` — show the command usage

Features: `evaluate` (prompt-quality coaching), `correct` (fix your TARGET-language
writing), `translate` (render NATIVE-language input in the target language).

You can pass several at once: `enable correct translate` (= auto: correct what you
write in the target language, translate what you write in your native one);
`disable correct translate` turns all language coaching off. Separators are flexible:
space, comma, or hyphen (`disable correct,translate`, `power-off`).

The change is written to a small state file the hook reads on every prompt, so it
takes effect immediately — no restart.
