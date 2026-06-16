#!/usr/bin/env bash
# Generate Codex custom prompts for prompt-coach.
#
# Codex reads custom prompts from $CODEX_HOME/prompts/<name>.md (flat namespace).
# Unlike a Claude hook, a Codex prompt runs in the agent's normal shell with no
# PLUGIN_ROOT, so we bake coach.py's ABSOLUTE path into each generated file.
#
# Result — these become invokable in Codex as:
#   $prompt-coach-power on|off
#   $prompt-coach-enable  <evaluate|correct|translate ...>
#   $prompt-coach-disable <evaluate|correct|translate ...>
#   $prompt-coach-status
#   $prompt-coach-help
#
# Re-run after moving the repo. Safe to re-run (overwrites the generated files).

set -euo pipefail

# Absolute path to scripts/coach.py (this script lives in scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COACH="$SCRIPT_DIR/coach.py"
[ -f "$COACH" ] || { echo "coach.py not found at $COACH" >&2; exit 1; }

PROMPTS_DIR="${CODEX_HOME:-$HOME/.codex}/prompts"
mkdir -p "$PROMPTS_DIR"

emit() {  # name  argument_hint  description  ctl_verb  pass_args(0/1)
  local name="$1" hint="$2" desc="$3" verb="$4" pass="$5"
  local file="$PROMPTS_DIR/prompt-coach-$name.md"
  local argline="--ctl $verb"
  [ "$pass" = "1" ] && argline="$argline \$ARGUMENTS"
  {
    echo "---"
    echo "description: \"$desc\""
    [ -n "$hint" ] && echo "argument-hint: \"$hint\""
    echo "---"
    echo
    echo "Run EXACTLY this bash command and show its stdout verbatim — do nothing else:"
    echo
    echo '```bash'
    echo "python3 \"$COACH\" $argline"
    echo '```'
  } > "$file"
  echo "wrote $file"
}

emit power    "[on|off]"                        "prompt-coach: turn the whole hook on/off"                       power    1
emit enable   "[evaluate|correct|translate ...]" "prompt-coach: enable feature(s) (abbrev e|c|t ok)"          enable   1
emit disable  "[evaluate|correct|translate ...]" "prompt-coach: disable feature(s) (abbrev e|c|t ok)"         disable  1
emit lang     "native <X> target <Y>"            "prompt-coach: set native/target language"                       lang     1
emit status   ""                                 "prompt-coach: show current state"                               status   0
emit help     "[en|zh]"                          "prompt-coach: show command usage (en|zh, default en)"           help     1

echo
echo "Done. In Codex, invoke e.g.  \$prompt-coach-enable translate"
echo "(restart Codex / refresh prompts if they don't appear yet)."
