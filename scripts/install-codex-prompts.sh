#!/usr/bin/env bash
# Install Prompt Coach for Codex.
#
# Codex reads custom prompts from $CODEX_HOME/prompts/<name>.md (flat namespace).
# Unlike a Claude hook, a Codex prompt runs in the agent's normal shell with no
# PLUGIN_ROOT, so we bake coach.py's ABSOLUTE path into each generated file.
#
# The Codex hook itself is installed through Codex's plugin marketplace flow:
#   codex plugin marketplace add <local-marketplace-wrapper>
#   codex plugin add prompt-coach@prompt-coach-local
#
# Result — these become invokable in Codex as:
#   $prompt-coach-power on|off
#   $prompt-coach-enable  <evaluate|correct|translate ...>
#   $prompt-coach-disable <evaluate|correct|translate ...>
#   $prompt-coach-status
#   $prompt-coach-help
#
# Re-run after moving the repo. Safe to re-run (overwrites the generated files).
# Pass --prompts-only to skip plugin marketplace registration.

set -euo pipefail

INSTALL_PLUGIN=1
for arg in "$@"; do
  case "$arg" in
    --prompts-only|--skip-plugin)
      INSTALL_PLUGIN=0
      ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/install-codex-prompts.sh [--prompts-only]

Installs Prompt Coach for Codex:
  1. Generates $CODEX_HOME/prompts/prompt-coach-*.md control prompts.
  2. Registers this repo as a local Codex marketplace.
  3. Installs prompt-coach@prompt-coach-local so Codex can load hooks/hooks.json.

Options:
  --prompts-only, --skip-plugin  Only generate $prompt-coach-* prompts.
  -h, --help                    Show this help.
EOF
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

# Absolute path to scripts/coach.py (this script lives in scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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

json_has_marketplace() {  # json_file marketplace_name
  python3 - "$1" "$2" <<'PY'
import json
import sys

path, name = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
start = text.find("{")
if start == -1:
    sys.exit(1)
data = json.loads(text[start:])
sys.exit(0 if any(m.get("name") == name for m in data.get("marketplaces", [])) else 1)
PY
}

json_has_plugin() {  # json_file plugin_id
  python3 - "$1" "$2" <<'PY'
import json
import sys

path, plugin_id = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
start = text.find("{")
if start == -1:
    sys.exit(1)
data = json.loads(text[start:])
sys.exit(0 if any(p.get("pluginId") == plugin_id for p in data.get("installed", [])) else 1)
PY
}

if [ "$INSTALL_PLUGIN" = "1" ]; then
  CODEX_BIN="${CODEX_BIN:-codex}"
  MARKETPLACE_NAME="prompt-coach-marketplace"
  PLUGIN_SELECTOR="prompt-coach@$MARKETPLACE_NAME"
  CODEX_ROOT="${CODEX_HOME:-$HOME/.codex}"
  MARKETPLACE_ROOT="$CODEX_ROOT/local-marketplaces/$MARKETPLACE_NAME"
  PLUGIN_LINK="$MARKETPLACE_ROOT/plugins/prompt-coach"

  mkdir -p "$MARKETPLACE_ROOT/.claude-plugin" "$MARKETPLACE_ROOT/plugins"

  cat > "$MARKETPLACE_ROOT/.claude-plugin/marketplace.json" <<EOF
{
  "name": "$MARKETPLACE_NAME",
  "description": "Local marketplace for Prompt Coach development installs.",
  "version": "0.9.1",
  "owner": {
    "name": "tercel"
  },
  "plugins": [
    {
      "name": "prompt-coach",
      "description": "Dual-axis coaching on every Codex prompt: improves prompt quality and target-language expression.",
      "author": {
        "name": "tercel"
      },
      "source": "./plugins/prompt-coach"
    }
  ]
}
EOF

  if [ -L "$PLUGIN_LINK" ]; then
    ln -sfn "$REPO_ROOT" "$PLUGIN_LINK"
  elif [ -e "$PLUGIN_LINK" ]; then
    echo "cannot update $PLUGIN_LINK: path exists and is not a symlink" >&2
    exit 1
  else
    ln -s "$REPO_ROOT" "$PLUGIN_LINK"
  fi

  if command -v "$CODEX_BIN" >/dev/null 2>&1; then
    MARKETPLACES_JSON="$(mktemp)"
    PLUGINS_JSON="$(mktemp)"
    trap 'rm -f "$MARKETPLACES_JSON" "$PLUGINS_JSON"' EXIT

    "$CODEX_BIN" plugin marketplace list --json > "$MARKETPLACES_JSON"
    if json_has_marketplace "$MARKETPLACES_JSON" "$MARKETPLACE_NAME"
    then
      echo "Codex marketplace already registered: $MARKETPLACE_NAME"
    else
      "$CODEX_BIN" plugin marketplace add "$MARKETPLACE_ROOT"
    fi

    "$CODEX_BIN" plugin list --json > "$PLUGINS_JSON"
    if json_has_plugin "$PLUGINS_JSON" "$PLUGIN_SELECTOR"
    then
      echo "Codex plugin already installed: $PLUGIN_SELECTOR"
    else
      "$CODEX_BIN" plugin add "$PLUGIN_SELECTOR"
    fi
  else
    echo "codex CLI not found; skipped plugin install." >&2
    echo "Install manually after Codex is available:" >&2
    echo "  codex plugin marketplace add \"$MARKETPLACE_ROOT\"" >&2
    echo "  codex plugin add $PLUGIN_SELECTOR" >&2
  fi
else
  echo "Skipped Codex plugin install (--prompts-only)."
fi

echo
echo "Done. Restart Codex / refresh prompts and trust the hook if prompted."
echo "Then invoke e.g.  \$prompt-coach-enable translate"
