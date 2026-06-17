---
description: prompt-coach — enable coaching feature(s) (evaluate, correct, translate)
argument-hint: "[evaluate|correct|translate …]"
---

Run EXACTLY this Bash command and show its stdout verbatim — do nothing else:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/coach.py" --ctl enable $ARGUMENTS
```

`$ARGUMENTS` is one or more features: `evaluate` (prompt-quality coaching),
`correct` (fix your target-language writing), `translate` (render native-language
input in the target language). Pass several at once, e.g. `correct translate`
(= auto). Takes effect on your next prompt — no restart.
