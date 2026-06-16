---
description: prompt-coach — set your native and/or target (practiced) language
argument-hint: "native <X> target <Y>"
---

Run EXACTLY this Bash command and show its stdout verbatim — do nothing else:

```bash
python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/scripts/coach.py" --ctl lang $ARGUMENTS
```

`$ARGUMENTS` uses `native <X>` and/or `target <Y>` (either or both, any order),
e.g. `native Chinese target English`. Each language may be a full name or a code:
`zh`/`en`/`ja`/`jp`/`kr`… → Chinese/English/Japanese/Korean. `native` is the language
you speak (used for explanations); `target` is the language you're practicing.
Persists across sessions; overrides `COACH_NATIVE_LANG` / `COACH_TARGET_LANG`.
