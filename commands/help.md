---
description: prompt-coach — show the command usage (optional: en | zh, default en)
argument-hint: "[en|zh]"
---

Run EXACTLY this Bash command and show its stdout verbatim — do nothing else:

```bash
python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/scripts/coach.py" --ctl help $ARGUMENTS
```

`$ARGUMENTS` is optional: `zh` for Chinese, `en` (or empty) for English.
