---
description: prompt-coach — turn the whole coaching hook on or off
argument-hint: "[on|off]"
---

Run EXACTLY this Bash command and show its stdout verbatim — do nothing else:

```bash
python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/scripts/coach.py" --ctl power $ARGUMENTS
```

`$ARGUMENTS` is `on` or `off`. Turns the entire hook on/off; individual feature
states are preserved. Takes effect on your next prompt — no restart.
