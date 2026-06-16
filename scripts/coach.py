#!/usr/bin/env python3
"""Prompt Dual-Coach — a Claude Code and Codex UserPromptSubmit hook.

On every prompt you submit, this hook asks a fast OpenAI model to analyze two
independent axes and feed coaching back to you:

  1. prompt  — the prompt as an instruction to a coding assistant
               (specificity, file paths, success criteria, constraints).
               Returns an improved prompt + one teaching tip.
  2. language — your expression in your chosen TARGET language (not just English).
               Returns concrete corrections (original -> fix + why) AND a fully
               rewritten, natural version.

This is the reusable "brain". The delivery shell supports Claude Code and Codex;
the same analysis logic is meant to be reused later behind a terminal split-pane
or a floating panel.

Delivery modes (env COACH_MODE):
  - "annotate" (default): non-blocking. Injects the coaching as additionalContext
    so the active coding agent shows it and answers the improved prompt.
  - "block": blocking. Surfaces the coaching and blocks the prompt so you
    consciously resubmit the improved version (a stricter learning loop).

Backends (env COACH_BACKEND):
  - "auto" (default): use the current hook platform's CLI, then its API.
  - "cli" / "api": force the current hook platform's CLI / API.
  - "codex" / "openai": force Codex CLI / OpenAI API.
  - "claude" / "anthropic": force Claude CLI / Anthropic API.

Configuration (environment variables):
  COACH_BACKEND            "auto" (default) | "cli" | "api"
                           | "codex" | "openai" | "claude" | "anthropic"
  COACH_PLATFORM           "auto" (default) | "codex" | "claude"
  COACH_CODEX_BIN          path to the `codex` binary (default: found on PATH)
  COACH_CLAUDE_BIN         path to the `claude` binary (default: found on PATH)
  COACH_CLI_FLAGS          extra space-separated flags for `codex exec`
  OPENAI_API_KEY           required only for the "api" backend / fallback
  ANTHROPIC_API_KEY        enables the Anthropic API backend / fallback
  COACH_TARGET_LANG        target language to coach (default: "English")
  COACH_NATIVE_LANG        your native language, used for explanations
                           (default: auto-detected from locale, e.g. LANG;
                            fallback "English")
  COACH_LEVEL              proficiency — tunes feedback depth. Free text;
                           recommended Beginner | Intermediate | Advanced
                           (or CEFR A1-C2). (default: "Advanced")
  COACH_MODEL              override both backend models (default: CLI config /
                           "gpt-4o-mini" for API)
  COACH_CLI_MODEL          override only the Codex CLI model
  COACH_API_MODEL          override only the OpenAI API model
  COACH_ANTHROPIC_MODEL    override only the Anthropic API / Claude CLI model
  Coaching features — each independent on/off, overridden live by `/coach`:
  COACH_EVALUATE           on/off (default on) — prompt-quality coaching
  COACH_CORRECT            on/off (default on) — correct TARGET-language writing
  COACH_TRANSLATE          on/off (default off) — render NATIVE input in TARGET
                           correct + translate may BOTH be on (= auto: correct
                           target-language input, translate native input).
  COACH_MODE               "annotate" (default) | "block"
  COACH_MIN_PROMPT_CHARS   ultra-short multi-word floor (default: 6). Trivial
                           input (bare answers, dev commands, single tokens) is
                           skipped regardless; short-but-vague multi-word prompts
                           like "fix bug" are coached.
  COACH_CONTEXT_MESSAGES   recent turns of conversation context to include
                           (default: 6; set 0 to analyze the prompt in isolation)
  COACH_CONTEXT_CHARS      max characters of rendered context (default: 2000)
  COACH_TIMEOUT            backend timeout seconds (default: 25)
  COACH_STATE_SCOPE        "global" (default) | "project". Controls how widely a
                           `/coach` toggle applies: global = one shared switch;
                           project = isolated per CLAUDE_PROJECT_DIR. (Per-session
                           is not possible — see state_path().)
  COACH_DISABLE            set truthy to disable without uninstalling
  COACH_DEBUG              set truthy to print errors to stderr

Runtime toggle: the `/coach` command (power on/off | enable/disable evaluate|correct|
translate | status) writes a small state file (under CLAUDE_PLUGIN_DATA / PLUGIN_DATA,
else ~/.claude) that this hook reads on every prompt. It overrides COACH_DISABLE /
COACH_EVALUATE / COACH_CORRECT / COACH_TRANSLATE so you can flip behavior mid-session
with no restart.

Re-entrancy: the CLI backend's nested `codex exec` / `claude -p` call could
re-fire UserPromptSubmit and re-invoke this hook. We set COACH_NESTED=1 on the
child so the nested invocation exits immediately — no recursion.

Design rule: this hook must NEVER break your workflow. Any error (missing
backend, network failure, bad JSON) results in a clean exit 0 with no output.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

# ---------------------------------------------------------------------------
# Analysis contract (structured output schema enforced by both backends)
# ---------------------------------------------------------------------------

ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "language": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "has_issues": {"type": "boolean"},
                "corrections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "original": {"type": "string"},
                            "correction": {"type": "string"},
                            "explanation": {"type": "string"},
                        },
                        "required": ["original", "correction", "explanation"],
                    },
                },
                "improved": {"type": "string"},
            },
            "required": ["has_issues", "corrections", "improved"],
        },
        "prompt": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "has_issues": {"type": "boolean"},
                "improved": {"type": "string"},
                "guidance": {"type": "string"},
            },
            "required": ["has_issues", "improved", "guidance"],
        },
    },
    "required": ["language", "prompt"],
}

_SYS_HEADER = (
    "You are a dual-axis writing coach embedded in a developer's AI coding "
    "assistant. The developer is a native {native} speaker practicing {target} "
    "at {level} level. You are given the recent CONVERSATION so far (it may be "
    "empty) and the user's NEW prompt. Analyze the NEW prompt on TWO independent "
    "axes and return ONLY the JSON object.\n\n"
)

# Language axis — three behaviors, picked from the correct/translate switches.
_LANG_CORRECT = (
    "1. language — evaluate the {target}-language expression of the NEW prompt.\n"
    "   - has_issues: true if grammar, word choice, or naturalness can be improved.\n"
    "   - corrections: specific fixes. Each: original (the exact problematic span), "
    "correction (the fixed span), explanation (one short clause written in {native}, "
    "explaining the rule).\n"
    "   - improved: a fully rewritten, natural {target} version of the NEW prompt, "
    "keeping the technical meaning identical.\n"
    "   If the prompt is NOT written in {target}, or is already native-quality, set "
    "has_issues=false, corrections=[], improved=\"\".\n\n"
)
_LANG_TRANSLATE = (
    "1. language — the NEW prompt is written in the user's native {native}; render it "
    "in natural {target} and teach the rendering.\n"
    "   - has_issues: true if the prompt contains {native} worth rendering in {target}.\n"
    "   - corrections: the key phrase mappings. Each: original (the {native} span), "
    "correction (the natural {target} equivalent), explanation (one short clause in "
    "{native} on usage / why).\n"
    "   - improved: a full, natural {target} rendering of the prompt, preserving the "
    "technical meaning.\n"
    "   If the prompt is already written in {target} (nothing to render), set "
    "has_issues=false, corrections=[], improved=\"\".\n\n"
)
_LANG_AUTO = (
    "1. language — adapt to whichever language the NEW prompt is written in.\n"
    "   - If it is in {target}: CORRECT it — corrections as original→fixed spans, "
    "improved = polished natural {target}.\n"
    "   - If it is in the user's native {native}: RENDER it in {target} — corrections as "
    "{native}→{target} phrase mappings, improved = full natural {target} rendering.\n"
    "   - has_issues: true when there is anything to correct or render. Explanations are "
    "ALWAYS written in {native}.\n"
    "   If the prompt is already flawless {target}, set has_issues=false, corrections=[], "
    "improved=\"\".\n\n"
)
_LANG_AXIS = {
    "correct": _LANG_CORRECT,
    "translate": _LANG_TRANSLATE,
    "auto": _LANG_AUTO,
}

_PROMPT_AXIS = (
    "2. prompt — evaluate the NEW prompt as the next instruction in THIS conversation.\n"
    "   - has_issues: true ONLY if, given the conversation context, the prompt is "
    "still genuinely ambiguous or under-specified. Do NOT flag information already "
    "established earlier (file paths, prior decisions, the task at hand): a short "
    "follow-up that is clear in context has NO issues.\n"
    "   - improved: a rewrite a coding assistant can act on precisely IN THIS "
    "context. It may rely on established context and need not restate it. Preserve "
    "the user's intent; never invent requirements they did not state.\n"
    "   - guidance: ONE short sentence written in {native} teaching the single most "
    "useful improvement. Identify which ONE element is missing and name it "
    "explicitly — choose from: a file/location path, an error message or symptom, "
    "a success criterion (what 'done' looks like), or an implementation approach "
    "(which method/library). Teach that specific gap, not a generic 'be clearer'.\n"
    "   If already strong, set has_issues=false, improved=\"\", guidance=\"\".\n\n"
    "Be concise. Never answer or execute the prompt — only analyze it."
)

# Back-compat alias: the default (correction-mode) full template.
SYSTEM_TEMPLATE = _SYS_HEADER + _LANG_CORRECT + _PROMPT_AXIS

# Shape hint for backends without schema enforcement (the CLI). Harmless on the
# API path, which additionally enforces ANALYSIS_SCHEMA via output_config.format.
JSON_SHAPE_HINT = (
    "Return ONLY a JSON object — no markdown code fences, no commentary — with "
    "exactly this shape:\n"
    '{"language":{"has_issues":<bool>,"corrections":'
    '[{"original":<str>,"correction":<str>,"explanation":<str>}],"improved":<str>},'
    '"prompt":{"has_issues":<bool>,"improved":<str>,"guidance":<str>}}'
)


def _user_content(prompt, context=""):
    parts = []
    if context:
        parts.append(
            "<conversation_so_far>\n" + context + "\n</conversation_so_far>\n"
        )
    parts.append(
        "Analyze the user's NEW prompt below in light of the conversation above "
        "(if any). Do NOT answer, execute, or use any tools — only return the JSON "
        "analysis.\n\n"
        + JSON_SHAPE_HINT
        + "\n\n<new_prompt>\n" + prompt + "\n</new_prompt>"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no network, no SDK import)
# ---------------------------------------------------------------------------

def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


_ON_WORDS = ("1", "true", "yes", "on", "enable", "enabled")
_OFF_WORDS = ("0", "false", "no", "off", "disable", "disabled")


def _onoff(value):
    """Parse an on/off word. Returns True/False, or None if unrecognized."""
    v = (value or "").strip().lower()
    if v in _ON_WORDS:
        return True
    if v in _OFF_WORDS:
        return False
    return None


def _axis_flag(state_val, env_val, default=True):
    """Resolve a coaching-axis on/off: state file wins, then env, then default."""
    if state_val is not None:
        return bool(state_val)
    parsed = _onoff(env_val) if env_val is not None else None
    return default if parsed is None else parsed


def _dry_run_prompt(args, stdin_text):
    """Extract the prompt for --dry-run from CLI args (preferred) or stdin."""
    rest = [a for a in args if a != "--dry-run"]
    if rest:
        return " ".join(rest).strip()
    return (stdin_text or "").strip()


def _to_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Conversation-context extraction from the session transcript -----------

def extract_text_from_content(content):
    """Pull plain text out of a transcript message's `content` field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _role_content(obj):
    msg = obj.get("message")
    if isinstance(msg, dict):
        return msg.get("role"), msg.get("content")
    return obj.get("role") or obj.get("type"), obj.get("content")


def extract_messages_from_lines(lines):
    """Parse transcript JSONL lines into [(role, text)], keeping only text turns."""
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        role, content = _role_content(obj)
        if role not in ("user", "assistant"):
            continue
        text = extract_text_from_content(content)
        if text and text.strip():
            out.append((role, text))
    return out


def build_context(messages, current_prompt, max_messages, max_chars, per_msg_chars=600):
    """Render the last few turns into a compact transcript string."""
    msgs = [(r, t) for (r, t) in messages if t and t.strip()]
    # Drop a trailing user turn that just echoes the prompt we're analyzing.
    if (
        msgs
        and current_prompt
        and msgs[-1][0] == "user"
        and msgs[-1][1].strip() == current_prompt.strip()
    ):
        msgs = msgs[:-1]
    if max_messages > 0:
        msgs = msgs[-max_messages:]
    rendered = []
    for role, text in msgs:
        label = "User" if role == "user" else "Assistant"
        t = " ".join(text.split())  # collapse whitespace/newlines
        if len(t) > per_msg_chars:
            t = t[:per_msg_chars] + "…"
        rendered.append("%s: %s" % (label, t))
    out = "\n".join(rendered)
    if max_chars > 0 and len(out) > max_chars:
        out = "…" + out[-max_chars:]
    return out


def _read_tail_lines(path, max_bytes=65536):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            data = fh.read()
            nl = data.find(b"\n")  # drop the partial first line
            if nl != -1:
                data = data[nl + 1:]
        else:
            data = fh.read()
    return data.decode("utf-8", "replace").splitlines()


def read_recent_context(transcript_path, current_prompt, max_messages, max_chars):
    """Read the tail of the session transcript and render recent turns."""
    if not transcript_path or max_messages <= 0:
        return ""
    try:
        lines = _read_tail_lines(transcript_path)
    except OSError:
        return ""
    messages = extract_messages_from_lines(lines)
    return build_context(messages, current_prompt, max_messages, max_chars)


# ISO 639-1 code -> English language name, for naming the user's native language
# in the coaching prompt. Extend as needed.
_LANG_NAMES = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "it": "Italian", "ru": "Russian", "ar": "Arabic", "hi": "Hindi",
    "vi": "Vietnamese", "th": "Thai", "id": "Indonesian", "tr": "Turkish",
    "nl": "Dutch", "pl": "Polish", "uk": "Ukrainian", "sv": "Swedish",
    "fa": "Persian", "he": "Hebrew", "el": "Greek", "cs": "Czech",
    "ro": "Romanian", "hu": "Hungarian", "da": "Danish", "fi": "Finnish",
    "nb": "Norwegian", "no": "Norwegian", "ms": "Malay", "bn": "Bengali",
}


def detect_native_language(env, default="English"):
    """Infer the user's native language from POSIX locale env vars.

    Checks LC_ALL, LC_MESSAGES, LANG, LANGUAGE (in that order) and maps the
    leading language code (e.g. "zh_CN.UTF-8" -> "zh" -> "Chinese"). Returns
    `default` if nothing recognizable is found.
    """
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        raw = env.get(key)
        if not raw:
            continue
        # LANGUAGE can be "en_US:en"; LANG can be "zh_CN.UTF-8"
        code = raw.split(":")[0].split(".")[0].split("_")[0].strip().lower()
        if code in _LANG_NAMES:
            return _LANG_NAMES[code]
    return default


def detect_platform(env):
    """Detect the active hook platform, defaulting to Codex for standalone use."""
    explicit = (env.get("COACH_PLATFORM") or "").strip().lower()
    if explicit in ("codex", "claude"):
        return explicit
    # Codex injects BOTH PLUGIN_ROOT and CLAUDE_PLUGIN_ROOT (the latter for
    # claude-ecosystem compat), whereas Claude Code sets only CLAUDE_PLUGIN_ROOT.
    # So PLUGIN_ROOT is the unambiguous Codex signal and must be checked first.
    if env.get("PLUGIN_ROOT"):
        return "codex"
    if env.get("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    return "codex"


def state_path(env):
    """Path to the runtime state file toggled by the `/coach` command.

    Scope (env COACH_STATE_SCOPE):
      - "global" (default): one shared file — a toggle affects every session.
      - "project": keyed by CLAUDE_PROJECT_DIR / PROJECT_DIR (a stable per-project
        filename suffix), so different projects are independent. Both the hook and
        the `/coach` command see that env var, so they compute the same path.

    True per-session scope is not offered: the platform exposes session_id only in
    the hook's stdin payload, not as an env var, so the `/coach` command (a plain
    subprocess) has no reliable way to learn which session it is in.

    The file always lives in the plugin data dir (CLAUDE_PLUGIN_DATA / PLUGIN_DATA,
    else ~/.claude) — never inside the user's project, so it can't be committed.
    """
    base = (
        env.get("CLAUDE_PLUGIN_DATA")
        or env.get("PLUGIN_DATA")
        or os.path.join(os.path.expanduser("~"), ".claude")
    )
    name = "prompt-dual-coach-state.json"
    scope = (env.get("COACH_STATE_SCOPE") or "global").strip().lower()
    if scope == "project":
        proj = env.get("CLAUDE_PROJECT_DIR") or env.get("PROJECT_DIR") or env.get("PWD")
        if proj:
            digest = hashlib.sha1(proj.encode("utf-8")).hexdigest()[:12]
            name = "prompt-dual-coach-state.%s.json" % digest
    return os.path.join(base, name)


def load_state(env):
    """Read the runtime state file. Returns {} on any error (never breaks)."""
    try:
        with open(state_path(env), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_config(env):
    """Build a config dict from an environment-like mapping."""
    try:
        min_chars = int(env.get("COACH_MIN_PROMPT_CHARS", "6") or "6")
    except (TypeError, ValueError):
        min_chars = 6
    codex_bin = env.get("COACH_CODEX_BIN") or shutil.which(
        "codex", path=env.get("PATH")
    )
    claude_bin = env.get("COACH_CLAUDE_BIN") or shutil.which(
        "claude", path=env.get("PATH")
    )
    shared_model = (env.get("COACH_MODEL") or "").strip()
    target = env.get("COACH_TARGET_LANG", "English")
    native_explicit = env.get("COACH_NATIVE_LANG")
    native = native_explicit or detect_native_language(env)
    # Disable the language axis ONLY when the user EXPLICITLY declares their native
    # language to equal the target (a native practicing their own tongue — nothing
    # to correct). If native is merely auto-detected and happens to match the
    # target, keep it on: the user may be a non-native on a foreign-locale machine,
    # and for a true native the axis simply self-suppresses (has_issues=false).
    coach_language = not (
        bool(native_explicit)
        and native.strip().lower() == target.strip().lower()
    )
    # Every coaching feature is an independent on/off switch, written by `/coach`
    # to the state file (which overrides the env defaults). The language axis has
    # two such switches — correction and translation — that may BOTH be on:
    #   correct only  -> correct TARGET-language writing        (lang_mode "correct")
    #   translate only-> render NATIVE-language input in TARGET (lang_mode "translate")
    #   both on       -> do whichever fits each prompt          (lang_mode "auto")
    #   both off      -> language axis silent
    state = load_state(env)
    disabled = _flag(env.get("COACH_DISABLE", ""))
    if "enabled" in state:                 # explicit toggle wins over env
        disabled = not bool(state["enabled"])
    evaluate_on = _axis_flag(state.get("evaluate"), env.get("COACH_EVALUATE"), True)
    correct_on = _axis_flag(state.get("correct"), env.get("COACH_CORRECT"), True)
    translate_on = _axis_flag(state.get("translate"), env.get("COACH_TRANSLATE"), False)
    axis_language = correct_on or translate_on
    if correct_on and translate_on:
        lang_mode = "auto"
    elif translate_on:
        lang_mode = "translate"
    else:
        lang_mode = "correct"
    return {
        "backend": (env.get("COACH_BACKEND", "auto") or "auto").strip().lower(),
        "platform": detect_platform(env),
        "codex_bin": codex_bin,
        "claude_bin": claude_bin,
        "target": target,
        "native": native,
        "coach_language": coach_language,
        "evaluate_on": evaluate_on,
        "axis_language": axis_language,
        "correct_on": correct_on,
        "translate_on": translate_on,
        "level": env.get("COACH_LEVEL", "Advanced"),
        "cli_model": (env.get("COACH_CLI_MODEL") or shared_model).strip(),
        "api_model": (env.get("COACH_API_MODEL") or shared_model or "gpt-4o-mini").strip(),
        "anthropic_model": (
            env.get("COACH_ANTHROPIC_MODEL") or shared_model or "claude-haiku-4-5-20251001"
        ).strip(),
        "mode": (env.get("COACH_MODE", "annotate") or "annotate").strip().lower(),
        "lang_mode": lang_mode,
        "min_chars": min_chars,
        "context_messages": _to_int(env.get("COACH_CONTEXT_MESSAGES"), 6),
        "context_chars": _to_int(env.get("COACH_CONTEXT_CHARS"), 2000),
        "timeout": _to_float(env.get("COACH_TIMEOUT"), 25.0),
        "disabled": disabled,
        "debug": _flag(env.get("COACH_DEBUG", "")),
        "has_api_key": bool(env.get("OPENAI_API_KEY")),
        "has_anthropic_key": bool(env.get("ANTHROPIC_API_KEY")),
    }


def backend_available(cfg):
    """True if the configured backend can actually run."""
    backend = cfg["backend"]
    platform = cfg["platform"]
    if backend == "cli":
        return bool(cfg["claude_bin"] if platform == "claude" else cfg["codex_bin"])
    if backend == "api":
        return bool(
            cfg["has_anthropic_key"] if platform == "claude" else cfg["has_api_key"]
        )
    if backend == "codex":
        return bool(cfg["codex_bin"])
    if backend == "openai":
        return bool(cfg["has_api_key"])
    if backend == "claude":
        return bool(cfg["claude_bin"])
    if backend == "anthropic":
        return bool(cfg["has_anthropic_key"])
    if platform == "claude":
        return bool(cfg["claude_bin"] or cfg["has_anthropic_key"])
    return bool(cfg["codex_bin"] or cfg["has_api_key"])


def extract_json_text(text):
    """Pull the JSON object out of possibly fenced / chatty model output."""
    if not text or not text.strip():
        raise ValueError("empty model output")
    t = text.strip()
    if t.startswith("```"):
        t = t[3:]
        if t[:4].lower() == "json":
            t = t[4:]
        end = t.rfind("```")
        if end != -1:
            t = t[:end]
        t = t.strip()
    if not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            t = t[start:end + 1]
    return t


# Multi-word phrases whose meaning is fully carried by project/git/conversation
# state — coaching them is pure noise. (Single-word commands like "commit",
# "build", "lint" are already caught by the single-token rule below.)
_SKIP_PHRASES = frozenset({
    "build it", "test it", "run it", "ship it", "do it", "try it", "fix it",
    "run tests", "run test", "run build", "run lint", "run dev",
    "commit and push", "add and commit", "stage and commit",
    "install dependencies", "install deps", "install it",
    "go ahead", "looks good", "thank you", "thanks a lot",
})

# Unambiguous CLI prefixes (trailing space avoids matching English words like
# "github" or "gopher"). Deliberately excludes ambiguous tokens such as
# "make"/"go" so "make it better" / "go implement X" still get coached.
_CMD_PREFIXES = (
    "git ", "npm ", "npx ", "yarn ", "pnpm ", "cargo ",
    "pip ", "poetry ", "docker ", "kubectl ",
)


def should_skip(prompt, min_chars):
    """Cheap, deterministic pre-filter run before any model call.

    Skips only input that is unambiguously not worth coaching — slash/shell
    passthroughs, bare answers, flow-control words, known one-shot dev commands,
    single tokens, and ultra-short fragments. Multi-word natural-language
    prompts — even short, vague ones like "fix bug" or "review code" — pass
    through; the model (which reads recent conversation) decides whether they
    actually need coaching and stays silent on context-clear follow-ups.
    """
    if prompt is None:
        return True
    s = prompt.strip()
    if not s:
        return True
    if s[0] in ("/", "!"):   # slash command / shell passthrough
        return True
    # Normalize: lowercase, collapse whitespace, drop trailing punctuation.
    norm = " ".join(s.lower().split()).rstrip(" .!?,;:")
    if not norm:
        return True
    if norm.isdigit():       # "1" / "2" — answering a numbered choice
        return True
    if norm in _SKIP_PHRASES:
        return True
    if norm.startswith(_CMD_PREFIXES):
        return True
    words = norm.split()
    if len(words) <= 1:      # single token: command, answer, or pronoun fragment
        return True
    if len(s) < min_chars:   # ultra-short multi-word floor ("do x", "go on")
        return True
    return False


def parse_analysis_text(text):
    """Parse the model's JSON into a normalized analysis dict."""
    data = json.loads(text)
    lang = data.get("language") or {}
    prm = data.get("prompt") or {}
    return {
        "language": {
            "has_issues": bool(lang.get("has_issues")),
            "corrections": lang.get("corrections") or [],
            "improved": lang.get("improved") or "",
        },
        "prompt": {
            "has_issues": bool(prm.get("has_issues")),
            "improved": prm.get("improved") or "",
            "guidance": prm.get("guidance") or "",
        },
    }


def has_any_issues(analysis):
    return bool(
        analysis["language"]["has_issues"] or analysis["prompt"]["has_issues"]
    )


def format_coaching(analysis, cfg):
    """Render the human-facing coaching block (plain text, terminal-safe)."""
    lines = ["-- Prompt Dual-Coach --"]
    prm = analysis.get("prompt", {})
    if prm.get("has_issues"):
        lines.append("[Prompt quality]")
        if prm.get("improved"):
            lines.append("  -> " + prm["improved"])
        if prm.get("guidance"):
            lines.append("  tip: " + prm["guidance"])
    lang = analysis.get("language", {})
    if lang.get("has_issues"):
        if cfg.get("lang_mode", "correct") in ("translate", "auto"):
            lines.append("[%s]" % cfg["target"])
        else:
            lines.append("[Language: %s]" % cfg["target"])
        for c in lang.get("corrections", []):
            lines.append(
                '  x "%s" -> "%s"  (%s)'
                % (c.get("original", ""), c.get("correction", ""), c.get("explanation", ""))
            )
        if lang.get("improved"):
            lines.append("  improved: " + lang["improved"])
    return "\n".join(lines)


def build_additional_context(analysis, cfg, block):
    """Instruction injected into the active agent's context for annotate mode."""
    lines = [
        "[prompt-dual-coach] Coaching for the user, a %s speaker practicing %s (%s level)."
        % (cfg["native"], cfg["target"], cfg["level"]),
        "Display the coaching block below to the user VERBATIM at the very start "
        "of your reply, then answer their request normally.",
        "",
        block,
    ]
    prm = analysis.get("prompt", {})
    if prm.get("has_issues") and prm.get("improved"):
        lines += ["", "Answer this improved version of their request: " + prm["improved"]]
    return "\n".join(lines)


def build_delivery(analysis, cfg):
    """Return (stdout, stderr, exit_code) for the given analysis + config."""
    if not has_any_issues(analysis):
        return ("", "", 0)  # clean prompt -> stay silent

    block = format_coaching(analysis, cfg)

    if cfg["mode"] == "block":
        return ("", block + "\n", 2)

    # annotate (default): non-blocking context injection
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": build_additional_context(analysis, cfg, block),
        }
    }
    return (json.dumps(payload), "", 0)


# ---------------------------------------------------------------------------
# Model call (isolated so pure helpers stay testable without the SDK / network)
# ---------------------------------------------------------------------------

def _system(cfg):
    lang_axis = _LANG_AXIS.get(cfg.get("lang_mode", "correct"), _LANG_CORRECT)
    base = (_SYS_HEADER + lang_axis + _PROMPT_AXIS).format(
        native=cfg["native"], target=cfg["target"], level=cfg["level"]
    )
    if not cfg.get("coach_language", True):
        base += (
            "\n\nIMPORTANT: The user's native language IS the target language, so "
            "the language axis does not apply. Always return language with "
            'has_issues=false, corrections=[], improved="". Analyze ONLY the prompt '
            "axis."
        )
    return base


def gate_axes(analysis, cfg):
    """Zero out whichever coaching axes are off.

    The language axis is off when native==target (nothing to coach) OR the user
    turned it off (`/coach disable correct translate`). The prompt (evaluate) axis is
    off when the user ran `/coach disable evaluate`. Returns the analysis unchanged
    when both are on.
    """
    lang_on = cfg.get("coach_language", True) and cfg.get("axis_language", True)
    prompt_on = cfg.get("evaluate_on", True)
    if lang_on and prompt_on:
        return analysis
    return {
        "language": (
            analysis.get("language", {"has_issues": False, "corrections": [], "improved": ""})
            if lang_on
            else {"has_issues": False, "corrections": [], "improved": ""}
        ),
        "prompt": (
            analysis.get("prompt", {"has_issues": False, "improved": "", "guidance": ""})
            if prompt_on
            else {"has_issues": False, "improved": "", "guidance": ""}
        ),
    }


# Backward-compatible alias (older name; same behavior plus the prompt axis).
gate_language = gate_axes


def _analyze_cli(prompt, cfg, context=""):
    """Run analysis through `codex exec` using the user's existing Codex auth."""
    import subprocess

    with tempfile.TemporaryDirectory(prefix="prompt-dual-coach-") as tmpdir:
        schema_path = os.path.join(tmpdir, "analysis-schema.json")
        with open(schema_path, "w", encoding="utf-8") as schema:
            json.dump(ANALYSIS_SCHEMA, schema)
        # Capture ONLY the final agent message in a file. `codex exec` prints a
        # chatty session log (preamble + reasoning) to stdout that can contain
        # braces and corrupt JSON extraction; the last-message file is clean.
        last_message_path = os.path.join(tmpdir, "last-message.txt")
        cmd = [
            cfg["codex_bin"], "exec",
            "--ephemeral",
            "--sandbox", "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--output-schema", schema_path,
            "--output-last-message", last_message_path,
        ]
        if cfg["cli_model"]:
            cmd += ["--model", cfg["cli_model"]]
        extra = os.environ.get("COACH_CLI_FLAGS", "").split()
        if extra:
            cmd[2:2] = extra
        cmd.append("-")
        child_env = dict(os.environ)
        child_env["COACH_NESTED"] = "1"
        proc = subprocess.run(
            cmd,
            input=_system(cfg) + "\n\n" + _user_content(prompt, context),
            capture_output=True,
            text=True,
            timeout=cfg["timeout"],
            env=child_env,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "codex CLI exit %d: %s"
                % (
                    proc.returncode,
                    ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[:400],
                )
            )
        try:
            with open(last_message_path, encoding="utf-8") as last:
                final = last.read()
        except OSError:
            final = ""
    # Fall back to stdout if the last-message file was empty / not written.
    return parse_analysis_text(extract_json_text(final or proc.stdout))


def _analyze_api(prompt, cfg, context=""):
    """Run analysis through the OpenAI Responses API (needs OPENAI_API_KEY)."""
    from openai import OpenAI  # lazy: pure-function tests run without the SDK

    client = OpenAI(timeout=cfg["timeout"])
    resp = client.responses.create(
        model=cfg["api_model"],
        instructions=_system(cfg),
        input=_user_content(prompt, context),
        text={
            "format": {
                "type": "json_schema",
                "name": "prompt_dual_coach_analysis",
                "strict": True,
                "schema": ANALYSIS_SCHEMA,
            }
        },
    )
    return parse_analysis_text(extract_json_text(resp.output_text))


def _analyze_claude_cli(prompt, cfg, context=""):
    """Run analysis through Claude CLI using the user's existing Claude auth."""
    import subprocess

    cmd = [
        cfg["claude_bin"], "-p",
        "--strict-mcp-config",
        "--output-format", "json",
        "--model", cfg["anthropic_model"],
        "--append-system-prompt", _system(cfg),
    ]
    child_env = dict(os.environ)
    child_env["COACH_NESTED"] = "1"
    proc = subprocess.run(
        cmd,
        input=_user_content(prompt, context),
        capture_output=True,
        text=True,
        timeout=cfg["timeout"],
        env=child_env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "claude CLI exit %d: %s"
            % (
                proc.returncode,
                ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[:400],
            )
        )
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError("claude CLI returned an error result")
    return parse_analysis_text(extract_json_text(envelope.get("result", "")))


def _analyze_anthropic_api(prompt, cfg, context=""):
    """Run analysis through the Anthropic Messages API."""
    import anthropic

    client = anthropic.Anthropic()
    resp = client.with_options(timeout=cfg["timeout"]).messages.create(
        model=cfg["anthropic_model"],
        max_tokens=1024,
        system=_system(cfg),
        messages=[{"role": "user", "content": _user_content(prompt, context)}],
        tools=[{
            "name": "prompt_dual_coach_analysis",
            "description": "Return the dual-axis coaching analysis.",
            "input_schema": ANALYSIS_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "prompt_dual_coach_analysis"},
    )
    tool_block = next(
        (b for b in resp.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_block is None:
        raise RuntimeError("Anthropic API returned no tool_use block")
    return parse_analysis_text(json.dumps(getattr(tool_block, "input")))


def analyze(prompt, cfg, context=""):
    """Dispatch to an explicit backend or the active platform's CLI/API pair."""
    backend = cfg["backend"]
    platform = cfg["platform"]
    if backend == "api":
        return (
            _analyze_anthropic_api(prompt, cfg, context)
            if platform == "claude"
            else _analyze_api(prompt, cfg, context)
        )
    if backend == "cli":
        return (
            _analyze_claude_cli(prompt, cfg, context)
            if platform == "claude"
            else _analyze_cli(prompt, cfg, context)
        )
    if backend == "codex":
        return _analyze_cli(prompt, cfg, context)
    if backend == "openai":
        return _analyze_api(prompt, cfg, context)
    if backend == "claude":
        return _analyze_claude_cli(prompt, cfg, context)
    if backend == "anthropic":
        return _analyze_anthropic_api(prompt, cfg, context)
    if platform == "claude":
        if cfg["claude_bin"]:
            try:
                return _analyze_claude_cli(prompt, cfg, context)
            except Exception:
                if cfg["has_anthropic_key"]:
                    return _analyze_anthropic_api(prompt, cfg, context)
                raise
        return _analyze_anthropic_api(prompt, cfg, context)
    if cfg["codex_bin"]:
        try:
            return _analyze_cli(prompt, cfg, context)
        except Exception:
            if cfg["has_api_key"]:
                return _analyze_api(prompt, cfg, context)
            raise
    return _analyze_api(prompt, cfg, context)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Each coaching feature is an independent on/off switch -> a state-file key.
# The three coaching features (command word -> state-file key).
_FEATURES = {
    "evaluate": "evaluate",    # prompt-quality coaching
    "correct": "correct",      # correct target-language writing
    "translate": "translate",  # render native-language input in the target
}
_CTL_USAGE = (
    "prompt-dual-coach — /coach <action>\n"
    "  power on | power off          the whole hook\n"
    "  enable  <evaluate|correct|translate ...>   turn feature(s) on\n"
    "  disable <evaluate|correct|translate ...>   turn feature(s) off\n"
    "  status                        show current state\n"
    "  help                          show this usage\n"
    "Features: evaluate (prompt quality), correct (fix target-language writing),\n"
    "          translate (render native-language input in the target language).\n"
    "correct + translate both on = auto: correct target input, translate native.\n"
)


def _control(argv, env):
    """Handle `--ctl <action ...>` from the `/coach` command. Returns exit code.

    Appliance-style master switch plus enable/disable verbs (space, hyphen, or
    comma all separate tokens, so "disable correct,translate" works):
      power on / power off        the whole hook
      enable <feature ...>        turn one or more features ON
      disable <feature ...>       turn one or more features OFF
      status                      print the current state (no write)
      help                        print usage and exit

    Features: evaluate (prompt-quality), correct (fix target-language writing),
    translate (render native-language input in the target). correct + translate
    may both be on — then each prompt is auto-handled (correct if you wrote the
    target language, translate if you wrote your native one).
    """
    # Normalize separators so "disable correct,translate" / "power-off" all split.
    raw = [a for a in argv if a != "--ctl"]
    tokens = []
    for a in raw:
        tokens.extend(a.replace("-", " ").replace(",", " ").split())
    if not tokens:
        tokens = ["status"]
    action = tokens[0].lower()
    rest = [t.lower() for t in tokens[1:]]

    if action in ("help", "h"):   # also matches -h / --help (hyphens stripped above)
        sys.stdout.write(_CTL_USAGE)
        return 0

    state = load_state(env)
    if action == "power":
        val = _onoff(rest[0] if rest else None)
        if val is None:
            sys.stderr.write(_CTL_USAGE)
            return 2
        state["enabled"] = val
    elif action in ("enable", "disable"):
        if not rest or any(f not in _FEATURES for f in rest):
            sys.stderr.write(_CTL_USAGE)
            return 2
        for feature in rest:
            state[_FEATURES[feature]] = (action == "enable")
    elif action != "status":
        sys.stderr.write(_CTL_USAGE)
        return 2

    if action != "status":
        path = state_path(env)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        except OSError as exc:
            sys.stderr.write("could not write state file: %r\n" % (exc,))
            return 1

    cfg = load_config(env)
    scope = (env.get("COACH_STATE_SCOPE") or "global").strip().lower()
    print(
        "prompt-dual-coach: power %s | evaluate: %s | correct: %s | translate: %s"
        % (
            "off" if cfg["disabled"] else "on",
            "on" if cfg["evaluate_on"] else "off",
            "on" if cfg["correct_on"] else "off",
            "on" if cfg["translate_on"] else "off",
        )
    )
    print(
        "native %s, practicing %s | scope: %s | state file: %s"
        % (cfg["native"], cfg["target"], scope, state_path(env))
    )
    return 0


def dry_run(argv):
    """Local try-it mode: analyze a prompt and print the coaching block.

    Usage:  python3 coach.py --dry-run "your prompt here"
            echo "your prompt" | python3 coach.py --dry-run
    """
    prompt = _dry_run_prompt(argv, "" if sys.stdin.isatty() else sys.stdin.read())
    if not prompt:
        sys.stderr.write(
            'usage: coach.py --dry-run "your prompt"  (or pipe text on stdin)\n'
        )
        return 2
    cfg = load_config(os.environ)
    if not backend_available(cfg):
        sys.stderr.write(
            "No backend available for %s: install its CLI or configure its API key.\n"
            % cfg["platform"]
        )
        return 1
    try:
        analysis = gate_axes(analyze(prompt, cfg), cfg)
    except Exception as exc:
        sys.stderr.write("analysis failed: %r\n" % (exc,))
        return 1
    if not has_any_issues(analysis):
        print("[prompt-dual-coach] No issues found — looks good.")
        return 0
    print(format_coaching(analysis, cfg))
    return 0


def main():
    # Control surface for the `/coach` command (toggle on/off, switch mode).
    if "--ctl" in sys.argv[1:]:
        sys.exit(_control(sys.argv[1:], os.environ))

    # Local try-it mode (user-invoked), checked before the re-entrancy guard.
    if "--dry-run" in sys.argv[1:]:
        sys.exit(dry_run(sys.argv[1:]))

    # Re-entrancy guard: we are inside our own nested `codex exec` call.
    if _flag(os.environ.get("COACH_NESTED", "")):
        sys.exit(0)

    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    cfg = load_config(os.environ)
    if cfg["disabled"] or not backend_available(cfg):
        sys.exit(0)

    prompt = event.get("prompt", "")
    if should_skip(prompt, cfg["min_chars"]):
        sys.exit(0)

    context = read_recent_context(
        event.get("transcript_path"),
        prompt,
        cfg["context_messages"],
        cfg["context_chars"],
    )

    try:
        analysis = analyze(prompt, cfg, context)
    except Exception as exc:  # never break the user's workflow
        if cfg["debug"]:
            sys.stderr.write("prompt-dual-coach error: %r\n" % (exc,))
        sys.exit(0)

    analysis = gate_axes(analysis, cfg)
    out, err, code = build_delivery(analysis, cfg)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    sys.exit(code)


if __name__ == "__main__":
    main()
