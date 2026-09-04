#!/usr/bin/env python3
"""Prompt Coach — a Claude Code and Codex UserPromptSubmit hook.

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
  - "annotate" (default): non-blocking. Emits the coaching on BOTH the
    display-only systemMessage channel and, as an echo instruction, through
    additionalContext — no single channel reaches every client (the terminal
    CLI renders systemMessage; the Claude desktop Code tab does not). The agent
    still answers your original prompt unchanged either way. Narrow it with
    COACH_ANNOTATE_CHANNEL = both (default) | system | context.
  - "block": blocking. Surfaces the coaching and blocks the prompt so you
    consciously resubmit the improved version (a stricter learning loop).

Backends (env COACH_BACKEND, or persistent `/prompt-coach:backend <choice>`):
  - "auto" (default): use the current hook platform's CLI, then its API. The CLI
    spawns a nested agent — robust but slow (often 15-25s+); prefer a direct
    backend below if it straddles COACH_TIMEOUT and silently drops coaching.
  - "cli" / "api": force the current hook platform's CLI / API.
  - "codex" / "openai": force Codex CLI / OpenAI API.
  - "claude" / "anthropic": force Claude CLI / Anthropic API.
  - "ollama": force a local Ollama server (COACH_OLLAMA_HOST / COACH_OLLAMA_MODEL).
    The model must be one you've pulled, or the call fails and the hook stays
    silent. Set it via `/prompt-coach:backend ollama <model>` — the choice persists
    in the cross-platform global config file (config.json), shared by Claude+Codex.

Configuration (environment variables):
  COACH_BACKEND            "auto" (default) | "cli" | "api" | "ollama"
                           | "codex" | "openai" | "claude" | "anthropic"
                           (also settable persistently via `/prompt-coach:backend`)
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
  COACH_OLLAMA_HOST        Ollama server base URL (default: http://localhost:11434)
  COACH_OLLAMA_MODEL       Ollama model for the "ollama" backend (default: llama3.1;
                           for quality try a strong instruct model, e.g.
                           qwen2.5-coder:32b-instruct-q4_K_M)
  COACH_OLLAMA_KEEP_ALIVE  how long Ollama keeps the model resident between calls
                           (default: 30m) — avoids repeated cold model-loads
  COACH_OLLAMA_AUTOSTART   on/off (default on) — if the "ollama" backend can't
                           reach COACH_OLLAMA_HOST, spawn `ollama serve` in the
                           background and wait briefly for it to come up. A
                           healthy server is never touched (one cheap liveness
                           probe per call, no spawn), and a cooldown file stops
                           a missing binary / crash-looping daemon from being
                           relaunched on every prompt.
  COACH_OLLAMA_BIN         path to the `ollama` binary for autostart (default:
                           found on PATH)
  COACH_OLLAMA_AUTOSTART_WAIT      seconds to wait for the spawned server to
                           become reachable before giving up on THIS call
                           (default: 8) — a slow/failed start still falls back
                           to the normal swallowed-error behavior.
  COACH_OLLAMA_AUTOSTART_COOLDOWN  minimum seconds between autostart attempts
                           (default: 60)
  COACH_OLLAMA_REQUIRE_LOADED  on/off (default on) — before analyzing, check
                           `/api/ps` that the model is already resident. If it
                           isn't, hand the cold load to a detached background
                           warm-up and skip coaching for THIS prompt instead of
                           cold-loading a multi-GB model inside COACH_TIMEOUT.
                           Turning this off restores the old behavior, where a
                           cold model made every prompt a long, CPU-pinning
                           request that usually timed out anyway.
  COACH_OLLAMA_WARM_TTL    seconds a background warm-up may run before its lock
                           is treated as stale (default: 900) — a big model on
                           a slow disk can legitimately take minutes to load.
  Coaching features — each independent on/off, overridden live by `/prompt-coach:*`.
  ALL default OFF (opt-in): a freshly-installed plugin does nothing until you turn
  a feature on (and when all are off the hook exits before any model call).
  COACH_EVALUATE           on/off (default off) — prompt-quality coaching
  COACH_CORRECT            on/off (default off) — correct TARGET-language writing
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
  COACH_CONTEXT_PER_MSG_CHARS  max chars kept per context message (default: 600).
                           Fenced code blocks are replaced with a [code] marker and
                           truncation snaps to a word boundary, so the budget holds
                           meaning, not half-cut code.
  COACH_MAX_PROMPT_CHARS   cap the analyzed prompt; longer prompts are sent as a
                           head+tail excerpt (default: 4000). Stops a pasted log
                           from blowing past COACH_TIMEOUT.
  COACH_SKIP_LANG_ON_PASTE on/off (default on) — when the prompt is mostly a log /
                           stack trace / code dump, skip the language axis (don't
                           "correct the grammar" of machine output).
  COACH_TIMEOUT            backend timeout seconds (default: 60). The nested CLI
                           backend can take 15-25s+; too low a value silently
                           drops coaching when the call overruns it.
  COACH_STATE_SCOPE        "project" (default) | "global". Controls how widely a
                           `/prompt-coach:*` toggle applies: project = isolated per
                           CLAUDE_PROJECT_DIR; global = one shared switch. (Per-session
                           is not possible — see state_path().)
  COACH_STATE_DIR          dir for the runtime state file (default:
                           ~/.config/prompt-coach). Must NOT depend on
                           CLAUDE_PLUGIN_DATA / PLUGIN_DATA — those differ between
                           the hook and the control command, which would desync them.
  COACH_DISABLE            set truthy to disable without uninstalling
  COACH_DEBUG              set truthy to print errors to stderr

Runtime toggle: the `/prompt-coach:power|enable|disable|status|help` commands write a
small state file in prompt-coach's fixed home path (see state_path())
that this hook reads on every prompt. They override COACH_DISABLE / COACH_EVALUATE /
COACH_CORRECT / COACH_TRANSLATE so you can flip behavior mid-session with no restart.

Re-entrancy: the CLI backend's nested `codex exec` / `claude -p` call could
re-fire UserPromptSubmit and re-invoke this hook. We set COACH_NESTED=1 on the
child so the nested invocation exits immediately — no recursion.

Design rule: this hook must NEVER break your workflow. Any error (missing
backend, network failure, bad JSON) results in a clean exit 0 with no output.
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

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
    "1. language — the NEW prompt is written in the user's native {native} (possibly "
    "MIXED with some {target}); render the WHOLE prompt in natural {target} and teach "
    "the rendering.\n"
    "   - has_issues: true if ANY part of the prompt is in {native} worth rendering in "
    "{target}. A prompt that mixes {native} and {target} STILL has issues — do not skip "
    "it just because some spans are already in {target}.\n"
    "   - corrections: 2-4 of the MOST instructive phrase mappings only — vocabulary "
    "choice, an idiom, tone, or a grammar point worth learning. Do NOT segment the whole "
    "sentence into consecutive spans that together just reconstruct 'improved' piece by "
    "piece; that duplicates it with no teaching value — skip trivial/obvious spans (a "
    "literal proper noun, a URL, a number). Each: original (the {native} span), "
    "correction (the natural {target} equivalent), explanation (one short clause in "
    "{native} on usage / why).\n"
    "   - improved: a full, natural {target} rendering of the ENTIRE prompt, translating "
    "every {native} span and integrating the existing {target} spans into one coherent "
    "{target} prompt, preserving the technical meaning.\n"
    "   Only if the prompt is ENTIRELY in {target} (nothing to render) set "
    "has_issues=false, corrections=[], improved=\"\".\n\n"
)
_LANG_AUTO = (
    "1. language — adapt to whichever language the NEW prompt is written in.\n"
    "   - If it is in {target}: CORRECT it — corrections as original→fixed spans, "
    "improved = polished natural {target}.\n"
    "   - If it is in the user's native {native}: RENDER it in {target} — corrections as "
    "{native}→{target} phrase mappings, improved = full natural {target} rendering.\n"
    "   - If it MIXES {native} and {target}: RENDER the whole thing in {target} — "
    "translate every {native} span and integrate the existing {target} spans into one "
    "coherent {target} prompt. A mixed prompt always has issues; never skip it just "
    "because some spans are already in {target}.\n"
    "   - When rendering (native or mixed cases), corrections are 2-4 of the MOST "
    "instructive mappings only — never a full sequential segmentation of the sentence; "
    "that just duplicates 'improved' piece by piece with no teaching value.\n"
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
    "context.{improved_lang} It may rely on established context and need not "
    "restate it. Preserve the user's intent; never invent requirements they did "
    "not state.\n"
    "   - guidance: ONE short sentence written in {native} teaching the single most "
    "useful improvement. Identify which ONE element is missing and name it "
    "explicitly — choose from: a file/location path, an error message or symptom, "
    "a success criterion (what 'done' looks like), or an implementation approach "
    "(which method/library). Teach that specific gap, not a generic 'be clearer'.\n"
    "   If already strong, set has_issues=false, improved=\"\", guidance=\"\".\n\n"
    "Be concise. Never answer or execute the prompt — only analyze it."
)

# Injected into _PROMPT_AXIS when the language axis is ON. The user is practicing
# {target}, so the actionable rewrite they are meant to reuse must itself be in
# {target} — a {native} or code-switched rewrite is not something they can send.
# Without this, the model mirrors whatever language the prompt came in.
_IMPROVED_IN_TARGET = (
    " Write it ENTIRELY in {target}, even when the NEW prompt is in {native} or "
    "mixes {native} and {target} — no {native} span may survive in it."
)

# Back-compat alias: the default (correction-mode) full template.
SYSTEM_TEMPLATE = (_SYS_HEADER + _LANG_CORRECT + _PROMPT_AXIS).replace(
    "{improved_lang}", ""
)

# Shape hint for backends without schema enforcement (the CLI). Harmless on the
# API path, which additionally enforces ANALYSIS_SCHEMA via output_config.format.
JSON_SHAPE_HINT = (
    "Return ONLY a JSON object — no markdown code fences, no commentary — with "
    "exactly this shape:\n"
    '{"language":{"has_issues":<bool>,"corrections":'
    '[{"original":<str>,"correction":<str>,"explanation":<str>}],"improved":<str>},'
    '"prompt":{"has_issues":<bool>,"improved":<str>,"guidance":<str>}}\n'
    'Inside every string value, escape literal double-quote characters as \\" '
    "(or use single quotes /「」 instead) — an unescaped \" breaks JSON parsing."
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


# Fenced code block (```...```), non-greedy, across lines.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _strip_code_blocks(text):
    """Replace fenced code blocks with a short placeholder.

    Keeps the *semantics* ("there was code here") while freeing the truncation
    budget for prose — so a long assistant code reply doesn't crowd out the
    conversational signal the coach actually needs.
    """
    return _FENCE_RE.sub(" [code] ", text or "")


def _truncate_words(text, limit):
    """Trim to <=limit chars, preferring a word boundary so we don't cut a word
    in half (which can garble meaning). Appends an ellipsis when trimmed."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    if sp >= limit * 0.6:        # only snap back to a space if it's not too far
        cut = cut[:sp]
    return cut.rstrip() + "…"


def build_context(messages, current_prompt, max_messages, max_chars,
                  per_msg_chars=600, strip_code=True):
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
        if strip_code:
            text = _strip_code_blocks(text)
        t = " ".join(text.split())  # collapse whitespace/newlines
        t = _truncate_words(t, per_msg_chars)
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


def read_recent_context(transcript_path, current_prompt, max_messages, max_chars,
                        per_msg_chars=600):
    """Read the tail of the session transcript and render recent turns."""
    if not transcript_path or max_messages <= 0:
        return ""
    try:
        lines = _read_tail_lines(transcript_path)
    except OSError:
        return ""
    messages = extract_messages_from_lines(lines)
    return build_context(
        messages, current_prompt, max_messages, max_chars, per_msg_chars
    )


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

# Common everyday aliases beyond strict ISO 639-1 (what people actually type).
_LANG_ALIASES = {
    "jp": "Japanese", "kr": "Korean", "cn": "Chinese",
    "zh-cn": "Chinese", "zh_cn": "Chinese", "ua": "Ukrainian",
}
# Canonical full names by lowercase, so "english"/"chinese" normalize to title case.
_LANG_FULLNAMES = {name.lower(): name for name in _LANG_NAMES.values()}


def normalize_language(value):
    """Map a language code/alias/full-name to a canonical English name.

    Accepts ISO codes (`zh`/`en`/`ja`), common aliases (`jp`/`kr`/`cn`), and full
    names in any case (`english` -> `English`). Unknown values pass through as
    typed (so any language name still works).
    """
    v = (value or "").strip()
    key = v.lower()
    return (
        _LANG_NAMES.get(key)
        or _LANG_ALIASES.get(key)
        or _LANG_FULLNAMES.get(key)
        or v
    )


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
    """Path to the runtime state file toggled by the `/prompt-coach:*` command.

    Scope (env COACH_STATE_SCOPE):
      - "project" (default): keyed by the project dir — file named
        state.<dir-basename>.<short-hash>.json (readable name + hash so same-named
        projects in different paths don't collide), and the full path is recorded
        inside the file. Both the hook and the command resolve the same path.
      - "global" (or project scope with no resolvable project dir): there is NO
        separate state file — feature toggles live in the single global config.json
        (config_path) alongside backend/language. One global file, not two.

    True per-session scope is not offered: the platform exposes session_id only in
    the hook's stdin payload, not as an env var, so the `/prompt-coach:*` command (a plain
    subprocess) has no reliable way to learn which session it is in.

    Location: a FIXED prompt-coach home dir (`~/.config/prompt-coach/`,
    overridable with COACH_STATE_DIR) — a dedicated folder so project-scoped
    files don't litter the tool's host homes. It must NOT depend on
    CLAUDE_PLUGIN_DATA / PLUGIN_DATA: those are set for the hook but NOT for the
    `/prompt-coach:*` command subprocess, so keying off them makes the command
    and the hook read different files — the command's toggles would silently
    never reach the hook. HOME is in both.
    """
    scope = (env.get("COACH_STATE_SCOPE") or "project").strip().lower()
    if scope == "project":
        proj = _project_dir(env)
        if proj:
            # Readable basename + short hash of the FULL path: the name shows which
            # project at a glance, while the hash keeps same-named projects in
            # different paths from colliding (and avoids over-long path-based names).
            slug = "".join(
                c if (c.isalnum() or c in "._-") else "-"
                for c in os.path.basename(proj.rstrip("/\\"))
            )[:40].strip("-") or "x"
            digest = hashlib.sha1(proj.encode("utf-8")).hexdigest()[:8]
            return os.path.join(_state_base_dir(env), "state.%s.%s.json" % (slug, digest))
    # Global scope, or project scope with no resolvable dir: the global config file
    # IS the feature store — no separate state.json.
    return config_path(env)


def _project_dir(env):
    """The project root used for project-scoped state (hook and command agree)."""
    return env.get("CLAUDE_PROJECT_DIR") or env.get("PROJECT_DIR") or env.get("PWD")


def _state_base_dir(env):
    """The prompt-coach home dir (COACH_STATE_DIR override, else ~/.config/prompt-coach)."""
    if env.get("COACH_STATE_DIR"):
        return env["COACH_STATE_DIR"]
    return os.path.join(os.path.expanduser("~"), ".config", "prompt-coach")


def config_path(env):
    """Path to the cross-platform GLOBAL config file (machine-wide settings:
    backend, ollama_*, native/target).

    Lives in the prompt-coach home dir, NOT in a host's settings, because
    ~/.claude/settings.json is Claude-only and ~/.codex/config.toml is Codex-only
    — neither is shared. Both platforms run this same coach.py under the same HOME,
    so a file here is the one place both hooks can read. Unlike state_path(), this
    is NEVER scoped: it is the single global config for every project/session.
    """
    return os.path.join(_state_base_dir(env), "config.json")


def load_state(env):
    """Read the runtime state file. Returns {} on any error (never breaks)."""
    try:
        with open(state_path(env), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_global_config(env):
    """Read the cross-platform global config file. Returns {} on any error."""
    try:
        with open(config_path(env), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_global_config(env, updates):
    """Merge `updates` into the global config file. Returns an error string or None."""
    conf = load_global_config(env)
    conf.update(updates)
    path = config_path(env)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(conf, fh)
    except OSError as exc:
        return "could not write config file: %r" % (exc,)
    return None


def _annotate_channel(env):
    """Which annotate-mode channel(s) to emit: "both" (default), "system", "context".

    Unrecognized values fall back to "both" rather than silencing coaching: a
    typo here must never be the reason nothing shows up.
    """
    value = (env.get("COACH_ANNOTATE_CHANNEL") or "both").strip().lower()
    return value if value in ("both", "system", "context") else "both"


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
    # Two config layers, plus env:
    #   - state: per-project (default scope) on/off feature toggles + power
    #   - gconf: the cross-platform GLOBAL config file (backend, ollama_*, language)
    # Resolution for shared settings: state > gconf > env > built-in default.
    # Feature toggles stay state-only (state > env) so they remain project-scoped.
    state = load_state(env)
    gconf = load_global_config(env)
    # native/target: state (per-project) > gconf (global, via `/prompt-coach:lang`)
    # > env > (native: locale).
    target = (
        state.get("target") or gconf.get("target")
        or env.get("COACH_TARGET_LANG") or "English"
    ).strip()
    native_explicit = (
        state.get("native") or gconf.get("native") or env.get("COACH_NATIVE_LANG")
    )
    native = (native_explicit or detect_native_language(env)).strip()
    # Disable the language axis ONLY when the user EXPLICITLY declares their native
    # language to equal the target (a native practicing their own tongue — nothing
    # to correct). If native is merely auto-detected and happens to match the
    # target, keep it on: the user may be a non-native on a foreign-locale machine,
    # and for a true native the axis simply self-suppresses (has_issues=false).
    coach_language = not (
        bool(native_explicit)
        and native.strip().lower() == target.strip().lower()
    )
    # The language axis has two switches — correction and translation — that may
    # BOTH be on:
    #   correct only  -> correct TARGET-language writing        (lang_mode "correct")
    #   translate only-> render NATIVE-language input in TARGET (lang_mode "translate")
    #   both on       -> do whichever fits each prompt          (lang_mode "auto")
    #   both off      -> language axis silent
    disabled = _flag(env.get("COACH_DISABLE", ""))
    if "enabled" in state:                 # explicit toggle wins over env
        disabled = not bool(state["enabled"])
    # Opt-in by default: everything OFF until the user enables a feature (per env
    # or `/prompt-coach:enable`). A freshly-installed plugin does nothing.
    evaluate_on = _axis_flag(state.get("evaluate"), env.get("COACH_EVALUATE"), False)
    correct_on = _axis_flag(state.get("correct"), env.get("COACH_CORRECT"), False)
    translate_on = _axis_flag(state.get("translate"), env.get("COACH_TRANSLATE"), False)
    axis_language = correct_on or translate_on
    if correct_on and translate_on:
        lang_mode = "auto"
    elif translate_on:
        lang_mode = "translate"
    else:
        lang_mode = "correct"
    # Backend: per-project state override > global config file (set via
    # `/prompt-coach:backend`) > env COACH_BACKEND > "auto" (CLI-preferring). "auto"
    # spawns the platform CLI; "api"/"ollama" hit a network endpoint directly
    # (faster, more reliable — no nested-agent spin-up).
    backend = (
        state.get("backend") or gconf.get("backend")
        or env.get("COACH_BACKEND") or "auto"
    ).strip().lower()
    return {
        "backend": backend,
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
        "ollama_host": (
            gconf.get("ollama_host") or env.get("COACH_OLLAMA_HOST")
            or "http://localhost:11434"
        ).strip().rstrip("/"),
        "ollama_model": (
            state.get("ollama_model") or gconf.get("ollama_model")
            or env.get("COACH_OLLAMA_MODEL") or shared_model or "llama3.1"
        ).strip(),
        "ollama_keep_alive": (
            gconf.get("ollama_keep_alive") or env.get("COACH_OLLAMA_KEEP_ALIVE") or "30m"
        ).strip(),
        "ollama_autostart": _flag(env.get("COACH_OLLAMA_AUTOSTART", "on")),
        "ollama_bin": (
            env.get("COACH_OLLAMA_BIN") or shutil.which("ollama", path=env.get("PATH")) or ""
        ),
        "ollama_autostart_wait": _to_float(env.get("COACH_OLLAMA_AUTOSTART_WAIT"), 8.0),
        "ollama_autostart_cooldown": _to_float(
            env.get("COACH_OLLAMA_AUTOSTART_COOLDOWN"), 60.0
        ),
        "ollama_require_loaded": _flag(env.get("COACH_OLLAMA_REQUIRE_LOADED", "on")),
        "ollama_warm_ttl": _to_float(env.get("COACH_OLLAMA_WARM_TTL"), 900.0),
        "mode": (env.get("COACH_MODE", "annotate") or "annotate").strip().lower(),
        "annotate_channel": _annotate_channel(env),
        "lang_mode": lang_mode,
        "min_chars": min_chars,
        "context_messages": _to_int(env.get("COACH_CONTEXT_MESSAGES"), 6),
        "context_chars": _to_int(env.get("COACH_CONTEXT_CHARS"), 2000),
        "context_per_msg_chars": _to_int(env.get("COACH_CONTEXT_PER_MSG_CHARS"), 600),
        "max_prompt_chars": _to_int(env.get("COACH_MAX_PROMPT_CHARS"), 4000),
        "skip_lang_on_paste": _flag(env.get("COACH_SKIP_LANG_ON_PASTE", "on")),
        "timeout": _to_float(env.get("COACH_TIMEOUT"), 60.0),
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
    if backend == "ollama":
        # A local server we can't cheaply probe here — assume reachable when
        # explicitly selected; a down server surfaces as a swallowed call error.
        return bool(cfg["ollama_host"] and cfg["ollama_model"])
    if platform == "claude":
        return bool(cfg["claude_bin"] or cfg["has_anthropic_key"])
    return bool(cfg["codex_bin"] or cfg["has_api_key"])


def required_api_sdk(cfg):
    """The pip package an API backend needs, or None for CLI/ollama backends.

    The SDKs (openai / anthropic) are OPTIONAL — imported lazily, only on the
    direct-API path. This names the dependency so `status` can flag a missing one
    instead of the hook failing silently at call time. "auto" is not flagged: it
    falls back to the CLI, which needs no SDK.
    """
    backend, platform = cfg["backend"], cfg["platform"]
    if backend == "openai":
        return "openai"
    if backend == "anthropic":
        return "anthropic"
    if backend == "api":
        return "anthropic" if platform == "claude" else "openai"
    return None


def _sdk_installed(name):
    """True if an importable module `name` is present (no import side effects)."""
    import importlib.util
    return importlib.util.find_spec(name) is not None


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


def _has_cjk(s):
    """True if the text contains Chinese / Japanese / Korean characters.

    These scripts are written without spaces between words, so whitespace word-
    counting (used by the single-token rule) would treat a whole sentence as one
    "word" and wrongly skip it.
    """
    for c in s:
        o = ord(c)
        if (
            0x4E00 <= o <= 0x9FFF      # CJK Unified Ideographs
            or 0x3400 <= o <= 0x4DBF   # CJK Extension A
            or 0x3040 <= o <= 0x30FF   # Hiragana + Katakana
            or 0xAC00 <= o <= 0xD7A3   # Hangul syllables
            or 0xFF66 <= o <= 0xFF9D   # half-width Katakana
        ):
            return True
    return False


def should_skip(prompt, min_chars):
    """Cheap, deterministic pre-filter run before any model call.

    Skips only input that is unambiguously not worth coaching — slash/shell
    passthroughs, bare answers, flow-control words, known one-shot dev commands,
    single tokens, and ultra-short fragments. Multi-word natural-language
    prompts — even short, vague ones like "fix bug" or "review code" — pass
    through; the model (which reads recent conversation) decides whether they
    actually need coaching and stays silent on context-clear follow-ups.

    CJK (Chinese/Japanese/Korean) text has no word spaces, so it is judged by
    character count instead of word count — otherwise a whole sentence would look
    like one token and be skipped (which broke translate mode for those users).
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
    if _has_cjk(norm):       # space-less script: judge by length, not word count
        return len(norm) < 2
    words = norm.split()
    if len(words) <= 1:      # single token: command, answer, or pronoun fragment
        return True
    if len(s) < min_chars:   # ultra-short multi-word floor ("do x", "go on")
        return True
    return False


# Lines that look like machine output rather than prose: log timestamps/levels,
# stack frames, tracebacks, and lines that open/close with code punctuation.
_PASTE_LINE_RE = re.compile(
    r"""^\s*(
        \d{4}-\d{2}-\d{2}                       # date  2024-01-02
      | \d{1,2}:\d{2}:\d{2}                      # time  12:34:56
      | \[?(ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|CRITICAL)\b  # log level
      | at\s+[\w$.]+\(                           # JS/Java frame: at foo.bar(
      | File\s+".*",\s*line\s+\d+                # Python traceback frame
      | Traceback\b
      | [\w.]+(Error|Exception)\b                # FooError / mod.BarException
      | [{}()\[\];]                              # line starting with code punctuation
    )""",
    re.VERBOSE | re.IGNORECASE,
)


def _is_paste_dominant(prompt, min_lines=8, ratio=0.5):
    """Heuristic: is this prompt mostly a pasted log / stack trace / code dump?

    Used to suppress the language axis — correcting the 'English grammar' of a
    stack trace is noise. Cheap and deterministic (no model call). A fenced code
    block, or a high fraction of log/stack/indented lines, marks a paste.
    """
    s = prompt or ""
    if "```" in s and s.count("```") >= 2:        # explicit fenced code block
        return True
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(lines) < min_lines:
        return False
    paste_like = 0
    for ln in lines:
        indent = len(ln) - len(ln.lstrip())
        if _PASTE_LINE_RE.search(ln) or indent >= 4:
            paste_like += 1
    return paste_like / len(lines) >= ratio


def _excerpt_prompt(prompt, max_chars):
    """Cap an over-long prompt to a head+tail excerpt before sending it to the
    model. The instruction usually sits at the start or end of a big paste, so
    keeping both ends preserves intent while bounding latency/cost."""
    s = prompt or ""
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    head_n = (max_chars * 3) // 5      # ~60% head
    tail_n = max_chars - head_n        # ~40% tail
    omitted = len(s) - head_n - tail_n
    return (
        s[:head_n].rstrip()
        + "\n\n…[%d characters trimmed]…\n\n" % omitted
        + s[-tail_n:].lstrip()
    )


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
    lines = ["-- Prompt Coach --"]
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
    """Coaching injected into the agent's context so the agent echoes it.

    This is a DISPLAY instruction, not a rewrite instruction. Up to 0.12.5 it
    also appended "Answer this improved version of their request: ..." — which
    made the agent answer a question the user never asked. 0.12.6 fixed that by
    dropping the channel entirely, and visibility went with it: `systemMessage`
    alone is rendered by the terminal CLI but NOT by the Claude desktop Code
    tab, so coaching silently vanished there. Echoing through the agent is the
    only delivery that reaches every client, so it comes back — without the
    line that caused the trouble.
    """
    _ = analysis   # kept for signature stability; the improved prompt is NOT injected
    return "\n".join([
        "[prompt-coach] Coaching for the user, a %s speaker practicing %s (%s level)."
        % (cfg["native"], cfg["target"], cfg["level"]),
        "Display the coaching block below to the user VERBATIM at the very start "
        "of your reply, then answer their ORIGINAL request unchanged. Do NOT "
        "answer the improved prompt, and do NOT comment on the coaching.",
        "",
        block,
    ])


def build_delivery(analysis, cfg):
    """Return (stdout, stderr, exit_code) for the given analysis + config."""
    if not has_any_issues(analysis):
        return ("", "", 0)  # clean prompt -> stay silent

    block = format_coaching(analysis, cfg)

    if cfg["mode"] == "block":
        return ("", block + "\n", 2)

    # annotate (default): no single channel reaches every client, so use both.
    #   systemMessage     — display-only, the agent never sees it. Rendered by
    #                       the terminal CLI.
    #   additionalContext — the agent echoes the block verbatim. The only thing
    #                       that shows up in a client that ignores
    #                       systemMessage (the Claude desktop Code tab).
    # COACH_ANNOTATE_CHANNEL narrows this to one channel for anyone who only
    # uses a client that renders systemMessage and doesn't want it twice.
    channel = cfg["annotate_channel"]
    payload = {}
    if channel in ("both", "system"):
        payload["systemMessage"] = block
    if channel in ("both", "context"):
        payload["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": build_additional_context(analysis, cfg, block),
        }
    return (json.dumps(payload), "", 0)


# ---------------------------------------------------------------------------
# Model call (isolated so pure helpers stay testable without the SDK / network)
# ---------------------------------------------------------------------------

_CJK_NATIVES = ("chinese", "japanese", "korean", "中文", "日本語", "한국어")


def _resolve_lang_mode(cfg, prompt=""):
    """Pick the language behavior for THIS prompt.

    For "auto" with a CJK native language, decide deterministically by script
    instead of trusting a small model to detect the input language: CJK input ->
    translate, otherwise -> correct. (Non-CJK natives keep the model-driven auto
    template.)
    """
    mode = cfg.get("lang_mode", "correct")
    if mode == "auto" and cfg.get("native", "").strip().lower() in _CJK_NATIVES:
        return "translate" if _has_cjk(prompt) else "correct"
    return mode


def _system(cfg, prompt=""):
    lang_axis = _LANG_AXIS.get(_resolve_lang_mode(cfg, prompt), _LANG_CORRECT)
    # The prompt axis mirrors the input language by default. When the user is also
    # practicing the target language, pin its rewrite to the target instead — the
    # rewrite is meant to be reusable as the actual prompt.
    _, lang_on = _active_axes(cfg)
    base = (_SYS_HEADER + lang_axis + _PROMPT_AXIS).format(
        native=cfg["native"],
        target=cfg["target"],
        level=cfg["level"],
        improved_lang=(
            _IMPROVED_IN_TARGET.format(native=cfg["native"], target=cfg["target"])
            if lang_on
            else ""
        ),
    )
    if not cfg.get("coach_language", True):
        base += (
            "\n\nIMPORTANT: The user's native language IS the target language, so "
            "the language axis does not apply. Always return language with "
            'has_issues=false, corrections=[], improved="". Analyze ONLY the prompt '
            "axis."
        )
    return base


def _active_axes(cfg):
    """The coaching axes currently ON, as (prompt_on, lang_on).

    SINGLE SOURCE OF TRUTH for "what's enabled" — both gate_axes() (what to keep)
    and _anything_to_coach() (whether to call the model at all) derive from this.
    MAINTENANCE: when you add a new coaching axis, add its flag here; forgetting
    to also gate it in gate_axes() would be obvious (it wouldn't be zeroed), so
    the early-exit optimization can't silently drop a new axis.
    """
    lang_on = cfg.get("coach_language", True) and cfg.get("axis_language", False)
    prompt_on = cfg.get("evaluate_on", False)
    return prompt_on, lang_on


def _anything_to_coach(cfg):
    """True if at least one axis is on — else the hook skips the model call."""
    return any(_active_axes(cfg))


def gate_axes(analysis, cfg):
    """Zero out whichever coaching axes are off.

    The language axis is off when native==target (nothing to coach) OR the user
    turned it off (`/prompt-coach:disable correct translate`). The prompt (evaluate) axis is
    off when the user ran `/prompt-coach:disable evaluate`. Returns the analysis unchanged
    when both are on.
    """
    prompt_on, lang_on = _active_axes(cfg)
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

    with tempfile.TemporaryDirectory(prefix="prompt-coach-") as tmpdir:
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
            input=_system(cfg, prompt) + "\n\n" + _user_content(prompt, context),
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
        instructions=_system(cfg, prompt),
        input=_user_content(prompt, context),
        text={
            "format": {
                "type": "json_schema",
                "name": "prompt_coach_analysis",
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
        "--append-system-prompt", _system(cfg, prompt),
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
        system=_system(cfg, prompt),
        messages=[{"role": "user", "content": _user_content(prompt, context)}],
        tools=[{
            "name": "prompt_coach_analysis",
            "description": "Return the dual-axis coaching analysis.",
            "input_schema": ANALYSIS_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "prompt_coach_analysis"},
    )
    tool_block = next(
        (b for b in resp.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_block is None:
        raise RuntimeError("Anthropic API returned no tool_use block")
    return parse_analysis_text(json.dumps(getattr(tool_block, "input")))


class OllamaNotReady(RuntimeError):
    """The local Ollama server/model isn't usable yet — skip coaching this turn.

    Raised instead of issuing a doomed request. `main` swallows every analysis
    error, so this degrades to "no coaching for this prompt" while a background
    warm-up brings the model up for the next one.

    `notice` carries a one-line message to show the user via `systemMessage`.
    It is set only on the prompt that actually kicks off a warm-up, so a cold
    start explains itself exactly once instead of silently eating coaching.
    """

    def __init__(self, message, notice=None):
        super().__init__(message)
        self.notice = notice


def _ollama_alive(host, timeout=1.0):
    """Cheap liveness probe for a local Ollama server. No side effects."""
    import urllib.request

    try:
        urllib.request.urlopen(host + "/api/version", timeout=timeout)
        return True
    except Exception:
        return False


def _ollama_model_resident(host, model, timeout=1.0):
    """True if `model` is already loaded in the server's memory (`/api/ps`).

    This is the gate that keeps the machine cool. A request against a
    NOT-resident model forces a synchronous cold load of the whole weight file
    inside the hook's timeout budget; when that budget runs out the client
    disconnects but the server keeps loading, and the next prompt stacks
    another request on top. Checking residency first lets us hand the load off
    to a detached warm-up instead of paying for it on the critical path.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(host + "/api/ps", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        # Old server without /api/ps, or a transient blip: don't block coaching
        # on a probe we can't trust — fall through to the normal request path.
        return True
    entries = payload.get("models")
    if not isinstance(entries, list):
        return True
    wanted = _normalize_model_tag(model)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in ("model", "name"):
            if _normalize_model_tag(entry.get(key) or "") == wanted:
                return True
    return False


def _normalize_model_tag(tag):
    """`qwen3:latest` and `qwen3` name the same model to Ollama; so treat them."""
    tag = (tag or "").strip()
    if tag.endswith(":latest"):
        tag = tag[: -len(":latest")]
    return tag


def _marker_path(env, name):
    return os.path.join(_state_base_dir(env), name)


def _on_cooldown(env, name, cooldown_secs):
    """True if `name` was marked too recently to retry.

    Each hook invocation is a fresh, short-lived process, so "don't retry too
    often" can't live in memory — it has to persist to disk. This is what
    stops a missing `ollama` binary or a crash-looping daemon from getting a
    fresh `ollama serve` spawn attempt on every single prompt.
    """
    try:
        age = time.time() - os.path.getmtime(_marker_path(env, name))
        return age < cooldown_secs
    except OSError:
        return False


def _mark(env, name):
    path = _marker_path(env, name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


def _clear_mark(env, name):
    try:
        os.remove(_marker_path(env, name))
    except OSError:
        pass


def _pid_alive(pid):
    """True if `pid` names a live process. Unknown/garbage PIDs read as dead."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True         # alive, just not ours to signal
    except OSError:
        return True         # can't tell — assume alive, TTL still bounds it
    return True


def _lock_holder_pid(path):
    """The PID recorded inside a lock file, or None if unreadable/not a PID."""
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _acquire_lock(env, name, ttl_secs, pid_aware=False):
    """Take a cross-process lock file, stealing it once it goes stale.

    O_EXCL makes creation atomic, so concurrent hook processes (several Claude
    Code sessions, or a fast double-send) can't both decide they're the one
    doing the work. `ttl_secs` bounds a lock orphaned by a killed process.

    `pid_aware` additionally steals a lock whose recorded holder is already
    dead, without waiting out the TTL. The host kills a hook that overruns its
    own (shorter) timeout, so a lock guarding work that lives and dies WITH the
    hook process is routinely orphaned; waiting out its TTL would then silence
    coaching for every prompt in that window. Only pass it for a lock that is
    meaningless once its holder exits — NOT for one whose TTL is deliberately a
    retry interval for detached work (the warm-up lock).
    """
    path = _marker_path(env, name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        return False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            fresh = time.time() - os.path.getmtime(path) < ttl_secs
            if fresh and not (pid_aware and not _pid_alive(_lock_holder_pid(path))):
                return False
            os.remove(path)  # stale — the holder died without releasing
        except OSError:
            return False
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            return False
    except OSError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    except OSError:
        pass
    finally:
        os.close(fd)
    return True


@contextlib.contextmanager
def _released_on_termination(env, name):
    """Release lock `name` if the host terminates us while inside the block.

    A UserPromptSubmit hook that overruns the host's own timeout is killed, so
    the `finally` that normally releases the lock never runs. Handling SIGTERM
    turns the common kill into a clean release; SIGKILL still can't be caught,
    which is what the pid_aware steal in `_acquire_lock` is for. Signals can
    only be installed on the main thread, so a failure to install is ignored —
    the pid_aware steal remains the backstop either way.
    """
    import signal

    previous = {}
    def _handler(signum, frame):
        _clear_mark(env, name)
        # Restore and re-raise so we die exactly as the host intended, instead
        # of turning a termination signal into a silently ignored one.
        try:
            signal.signal(signum, previous.get(signum, signal.SIG_DFL))
            os.kill(os.getpid(), signum)
        except (OSError, ValueError):
            os._exit(1)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            previous[signum] = signal.signal(signum, _handler)
        except (OSError, ValueError, AttributeError):
            previous.pop(signum, None)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass


def _autostart_ollama(cfg, env):
    """Best-effort background start of `ollama serve` if unreachable.

    Only spawns when the liveness probe actually fails — a healthy server is
    never touched, so the common case (already running) costs one fast local
    HTTP round-trip per prompt, not a process spawn. `start_new_session` (like
    a shell's `setsid`) detaches the daemon from this short-lived hook process
    so it keeps running after the hook exits.
    """
    import subprocess

    if _ollama_alive(cfg["ollama_host"]):
        return True
    if _on_cooldown(env, ".ollama_autostart_ts", cfg["ollama_autostart_cooldown"]):
        return False
    _mark(env, ".ollama_autostart_ts")
    if not cfg["ollama_bin"]:
        return False
    try:
        subprocess.Popen(
            [cfg["ollama_bin"], "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    deadline = time.time() + cfg["ollama_autostart_wait"]
    while time.time() < deadline:
        if _ollama_alive(cfg["ollama_host"], timeout=0.5):
            return True
        time.sleep(0.5)
    return False


# Loads the model into the server's memory and exits. `/api/generate` with an
# empty prompt is Ollama's documented load-only call: it returns as soon as the
# weights are resident, without generating a single token.
#
# The lock is released HERE, and only on success. That makes the lock track the
# real load — however long it takes — instead of a guessed duration, so
# COACH_OLLAMA_WARM_TTL is only ever a backstop for a killed warm-up, never a
# value anyone has to tune to their disk speed. On failure the lock is
# deliberately left in place: a model that isn't pulled would otherwise get a
# fresh warm-up spawned on every single prompt, and the TTL becomes the retry
# interval for exactly that case.
_WARM_SNIPPET = (
    "import json,os,sys,urllib.request\n"
    "host,model,keep,timeout,lock=sys.argv[1:6]\n"
    "body=json.dumps({'model':model,'prompt':'','keep_alive':keep}).encode('utf-8')\n"
    "req=urllib.request.Request(host+'/api/generate',data=body,"
    "headers={'Content-Type':'application/json'},method='POST')\n"
    "try:\n"
    "    urllib.request.urlopen(req,timeout=float(timeout)).read()\n"
    "except Exception:\n"
    "    sys.exit(1)\n"
    "try:\n"
    "    os.remove(lock)\n"
    "except OSError:\n"
    "    pass\n"
)


def _warm_ollama_async(cfg, env):
    """Kick off a detached, non-blocking model load. Returns True if spawned.

    The load runs in its own session so it survives this hook process exiting,
    and a TTL lock means a second prompt arriving mid-load does NOT spawn a
    competing load — which is exactly the pile-up that pinned the CPU before.
    The lock is only released once the model shows up as resident.
    """
    import subprocess

    if not _acquire_lock(env, ".ollama_warm.lock", cfg["ollama_warm_ttl"]):
        return False
    try:
        subprocess.Popen(
            [
                sys.executable, "-c", _WARM_SNIPPET,
                cfg["ollama_host"], cfg["ollama_model"],
                cfg["ollama_keep_alive"], str(cfg["ollama_warm_ttl"]),
                _marker_path(env, ".ollama_warm.lock"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        _clear_mark(env, ".ollama_warm.lock")
        return False
    return True


def _ensure_ollama_ready(cfg, env):
    """Gate the analysis call on a live server holding a resident model.

    Raises OllamaNotReady (silently skipping coaching for this prompt) rather
    than issuing a request that would cold-load the model on the critical path.
    """
    if not _ollama_alive(cfg["ollama_host"]):
        started = cfg["ollama_autostart"] and _autostart_ollama(cfg, env)
        if not started:
            raise OllamaNotReady("ollama server unreachable at %s" % cfg["ollama_host"])

    if not cfg["ollama_require_loaded"]:
        return
    if _ollama_model_resident(cfg["ollama_host"], cfg["ollama_model"]):
        # Resident now — whoever held the warm-up lock is done with it.
        _clear_mark(env, ".ollama_warm.lock")
        return
    # Only the prompt that actually starts the load explains itself; the lock
    # makes that exactly one prompt per cold start, not one per prompt.
    spawned = _warm_ollama_async(cfg, env)
    raise OllamaNotReady(
        "ollama model %r not resident; warming up in the background"
        % cfg["ollama_model"],
        notice=(
            "Prompt Coach: loading local model %s in the background. "
            "Coaching resumes automatically once it's resident "
            "(this happens once per cold start)." % cfg["ollama_model"]
        ) if spawned else None,
    )


def _analyze_ollama(prompt, cfg, context=""):
    """Run analysis through a local Ollama server (native /api/chat).

    Uses the stdlib only (no SDK) so the hook stays dependency-free. Structured
    output is requested via Ollama's `format` field set to the JSON schema, so the
    model returns a schema-conforming object directly in message.content.

    `think: false` disables extended reasoning on models that support it (e.g.
    qwen3-family thinking models). Without it, such a model burns tens to
    hundreds of seconds generating a hidden chain-of-thought before the actual
    JSON — routinely exceeding COACH_TIMEOUT and making the hook silently drop
    coaching (by design, any error is swallowed). Measured on qwen3.5:27b-mlx:
    16s with think=false vs. 73-280s+ without it, for identical output. Ollama
    ignores the field harmlessly on models without thinking support.

    `presence_penalty: 0` overrides whatever default a given model ships with
    (e.g. qwen3.5:9b-mlx defaults to 1.5). A high presence penalty punishes ANY
    repeated token, including structural JSON punctuation ({, }, ", :, ,) that
    necessarily repeats many times across a long schema response — on longer
    analyses (more correction items) this reliably pushed the model to emit an
    early stop token before closing the final braces, producing truncated,
    unparseable JSON. Reproduced deterministically at presence_penalty=1.5
    (eval_count 577-600, done_reason "stop", JSON missing its closing brace);
    presence_penalty=0 closed the JSON correctly every time.
    """
    import urllib.request

    # Never issue a request that would cold-load the model inline: that's what
    # made every prompt a multi-minute CPU burn when the server wasn't already
    # warm. This either returns fast against a resident model or raises.
    _ensure_ollama_ready(cfg, os.environ)

    # One in-flight analysis at a time. Without this, a prompt sent while the
    # previous analysis is still running stacks a second inference on the same
    # machine; a few of those in a row is the "computer gets very hot" symptom.
    #
    # pid_aware because this lock guards work that dies with the hook process,
    # and the host kills a hook that overruns ITS timeout (30s in hooks.json)
    # well before COACH_TIMEOUT (60s) is up. A killed holder must not silence
    # coaching for the rest of the TTL — the very failure that looks like
    # "the ollama backend stopped working entirely".
    if not _acquire_lock(
        os.environ, ".ollama_infer.lock", cfg["timeout"] + 30.0, pid_aware=True
    ):
        raise OllamaNotReady("another prompt-coach analysis is already in flight")

    body = json.dumps({
        "model": cfg["ollama_model"],
        "stream": False,
        "format": ANALYSIS_SCHEMA,
        "think": False,
        "options": {"temperature": 0, "presence_penalty": 0},
        # Keep the model resident between prompts so intermittent hook calls don't
        # repeatedly pay the cold model-load cost (Ollama unloads after 5m by default).
        "keep_alive": cfg["ollama_keep_alive"],
        "messages": [
            {"role": "system", "content": _system(cfg, prompt)},
            {"role": "user", "content": _user_content(prompt, context)},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["ollama_host"] + "/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # `finally` covers a normal error; the signal guard covers the host killing
    # us mid-inference, which is how this lock actually gets orphaned.
    with _released_on_termination(os.environ, ".ollama_infer.lock"):
        try:
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
                envelope = json.loads(resp.read().decode("utf-8"))
        finally:
            _clear_mark(os.environ, ".ollama_infer.lock")
    content = (envelope.get("message") or {}).get("content", "")
    return parse_analysis_text(extract_json_text(content))


def analyze(prompt, cfg, context=""):
    """Dispatch to an explicit backend or the active platform's CLI/API pair."""
    backend = cfg["backend"]
    platform = cfg["platform"]
    if backend == "ollama":
        return _analyze_ollama(prompt, cfg, context)
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

_ZH_LANGS = ("zh", "zh-cn", "zh_cn", "cn", "chinese", "中文")

# Backends selectable via `/prompt-coach:backend`. The headline three (auto, api,
# ollama) cover the common cases; the platform-specific names are kept for power
# users. "auto" (default) prefers the platform CLI; "api"/"ollama" call a network
# endpoint directly, avoiding the slow/fragile nested-agent spin-up.
_BACKENDS = (
    "auto", "cli", "api", "ollama",
    "codex", "openai", "claude", "anthropic",
)

# Accept the full feature name or its single-letter code (e|c|t). Deliberately
# small — three features with distinct initials; no plan to add more.
_FEATURE_ALIASES = {
    "e": "evaluate", "evaluate": "evaluate",
    "c": "correct", "correct": "correct",
    "t": "translate", "translate": "translate",
}


def _resolve_feature(token):
    """Resolve a feature token (full name or e|c|t) to its canonical name."""
    return _FEATURE_ALIASES.get((token or "").strip().lower())


_CTL_USAGE = (
    "prompt-coach — commands:\n"
    "  /prompt-coach:power on | off\n"
    "  /prompt-coach:enable  <evaluate|correct|translate ...>\n"
    "  /prompt-coach:disable <evaluate|correct|translate ...>\n"
    "  /prompt-coach:lang native <X> target <Y>   (set languages; name or code, e.g. native zh target en)\n"
    "  /prompt-coach:backend auto | cli | api | ollama [model]   (analysis engine; auto=CLI,\n"
    "      default. For ollama, pass a pulled model: backend ollama qwen2.5-coder:32b-instruct-q4_K_M)\n"
    "  /prompt-coach:status\n"
    "  /prompt-coach:help [en|zh]\n"
    "Features — give the FULL NAME or its single LETTER (both accepted, mixable):\n"
    "  evaluate  = e  — prompt-quality coaching\n"
    "  correct   = c  — fix target-language writing\n"
    "  translate = t  — render native-language input in the target language\n"
    "e.g.  enable c t   ==   enable correct translate\n"
    "correct + translate both on = auto: correct target input, translate native.\n"
    "Everything is OFF by default — enable what you want:\n"
    "  /prompt-coach:enable correct translate evaluate   (pick any subset)\n"
)

_CTL_USAGE_ZH = (
    "prompt-coach — 指令:\n"
    "  /prompt-coach:power on | off        总开关(开/关整个 hook)\n"
    "  /prompt-coach:enable  <功能 ...>     打开一个或多个功能\n"
    "  /prompt-coach:disable <功能 ...>     关闭一个或多个功能\n"
    "  /prompt-coach:lang native <母语> target <练的语言>   设置语言(全名或代码,如 native zh target en)\n"
    "  /prompt-coach:backend auto | cli | api | ollama [模型]   分析引擎(auto=CLI,默认;\n"
    "      选 ollama 要带已 pull 的模型,如 backend ollama qwen2.5-coder:32b-instruct-q4_K_M)\n"
    "  /prompt-coach:status                查看当前状态\n"
    "  /prompt-coach:help [en|zh]          查看用法(语言,默认 en)\n"
    "功能 —— 用「全名」或「单字母」均可(可混用):\n"
    "  evaluate  = e  — prompt 质量建议\n"
    "  correct   = c  — 纠正你的目标语言写作\n"
    "  translate = t  — 把母语输入翻成目标语言\n"
    "例:enable c t   等同   enable correct translate\n"
    "correct 与 translate 同时开 = 自动:打目标语就纠错,打母语就翻译。\n"
    "默认全部关闭 —— 按需开启:\n"
    "  /prompt-coach:enable correct translate evaluate   (任选其一或多个)\n"
)


def _cmd(env, rest):
    """Render a command reference in the active platform's syntax:
    Claude `/prompt-coach:enable …`  ·  Codex `$prompt-coach-enable …`."""
    pre = "$prompt-coach-" if detect_platform(env) == "codex" else "/prompt-coach:"
    return pre + rest


def _usage(env, zh=False):
    """Usage text with command references in the active platform's syntax."""
    text = _CTL_USAGE_ZH if zh else _CTL_USAGE
    if detect_platform(env) == "codex":
        text = text.replace("/prompt-coach:", "$prompt-coach-")
    return text


def _control(argv, env):
    """Handle `--ctl <action ...>` from the `/prompt-coach:*` command. Returns exit code.

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
    rest_raw = tokens[1:]                      # original case (for language values)
    rest = [t.lower() for t in rest_raw]

    if action in ("help", "h"):   # also matches -h / --help (hyphens stripped above)
        lang = rest[0] if rest else "en"
        sys.stdout.write(_usage(env, zh=lang in _ZH_LANGS))
        return 0

    # Where each action writes:
    #   power/enable/disable -> the scoped STATE file (per-project by default)
    #   lang/backend         -> the cross-platform GLOBAL config file (config.json)
    state = load_state(env)
    gconf_updates = {}                              # collected global-config writes
    write_to = None                                 # "state" | "global" | None(status)
    if action == "power":
        val = _onoff(rest[0] if rest else None)
        if val is None:
            sys.stderr.write(_usage(env))
            return 2
        state["enabled"] = val
        write_to = "state"
    elif action in ("enable", "disable"):
        # No features specified → default to all (supports Desktop where $ARGUMENTS is stripped).
        if not rest:
            rest = list(_FEATURES.keys())
        resolved = [_resolve_feature(f) for f in rest]
        if any(r is None for r in resolved):
            sys.stderr.write(_usage(env))
            return 2
        for feature in resolved:
            if feature is not None:   # always true after the guard above
                state[_FEATURES[feature]] = (action == "enable")
        write_to = "state"
    elif action == "lang":
        # `lang native <X> target <Y>` — either or both, order-free. Global: your
        # native/target language is machine-wide, shared across projects/platforms.
        i = 0
        while i + 1 < len(rest):
            if rest[i] in ("native", "target"):
                gconf_updates[rest[i]] = normalize_language(rest_raw[i + 1])
                i += 2
            else:
                break
        if not gconf_updates or i != len(rest):      # leftover/unknown tokens
            sys.stderr.write(_usage(env))
            return 2
        write_to = "global"
    elif action == "backend":
        # Read from `raw` (the un-split argv), NOT `rest`: the tokenizer turns "-"
        # into spaces, which would shatter a model name like
        # "qwen2.5-coder:32b-instruct-q4_K_M". raw = ["backend", choice, model?].
        choice = raw[1].lower() if len(raw) > 1 else None
        if choice not in _BACKENDS:
            sys.stderr.write(_usage(env))
            return 2
        gconf_updates["backend"] = choice
        # Optional Ollama model token, kept intact (hyphens preserved):
        #   /prompt-coach:backend ollama qwen2.5-coder:32b-instruct-q4_K_M
        if choice == "ollama" and len(raw) > 2 and raw[2].strip():
            gconf_updates["ollama_model"] = raw[2].strip()
        write_to = "global"
    elif action != "status":
        sys.stderr.write(_usage(env))
        return 2

    if write_to == "state":
        # Self-document which project a project-scoped file belongs to (the name
        # is hashed; this makes `cat state.<x>.json` tell you the path).
        if (env.get("COACH_STATE_SCOPE") or "project").strip().lower() == "project":
            proj = _project_dir(env)
            if proj:
                state["project"] = proj
        path = state_path(env)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        except OSError as exc:
            sys.stderr.write("could not write state file: %r\n" % (exc,))
            return 1
    elif write_to == "global":
        err = save_global_config(env, gconf_updates)
        if err:
            sys.stderr.write(err + "\n")
            return 1

    cfg = load_config(env)
    scope = (env.get("COACH_STATE_SCOPE") or "project").strip().lower()
    # Show the model when the backend is Ollama: it MUST be a model you've pulled,
    # or the call fails (and the hook stays silent). Naming it here makes the
    # requirement visible at a glance.
    backend_label = cfg["backend"]
    if cfg["backend"] == "ollama":
        backend_label = "ollama (%s)" % cfg["ollama_model"]
    print(
        "prompt-coach: power %s | evaluate: %s | correct: %s | translate: %s"
        % (
            "off" if cfg["disabled"] else "on",
            "on" if cfg["evaluate_on"] else "off",
            "on" if cfg["correct_on"] else "off",
            "on" if cfg["translate_on"] else "off",
        )
    )
    print(
        "native %s, practicing %s | backend: %s | scope: %s"
        % (cfg["native"], cfg["target"], backend_label, scope)
    )
    # In project scope there are two files (per-project features + global config);
    # in global scope they are the same single file.
    if state_path(env) == config_path(env):
        print("config (global, all settings): %s" % config_path(env))
    else:
        print("features (this project): %s" % state_path(env))
        print("global config (backend/lang, cross-platform): %s" % config_path(env))
    if cfg["backend"] == "ollama":
        print(
            "ollama: %s — ensure it's pulled (`ollama pull %s`); "
            "change with %s"
            % (cfg["ollama_model"], cfg["ollama_model"], _cmd(env, "backend ollama <model>"))
        )
    # Flag the hidden optional dependency for API backends instead of failing
    # silently when the SDK isn't installed.
    sdk = required_api_sdk(cfg)
    if sdk and not _sdk_installed(sdk):
        print(
            "backend '%s' needs the %s SDK (not installed) — `pip install %s`"
            % (cfg["backend"], sdk, sdk)
        )
    # Guide a new user out of the all-off default state.
    if cfg["disabled"]:
        print("hook is OFF — turn it on: " + _cmd(env, "power on"))
    elif not _anything_to_coach(cfg):
        print(
            "nothing enabled — enable what you want: "
            + _cmd(env, "enable correct translate evaluate")
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
    # Mirror the hook: suppress language coaching on a pasted log/code dump, and
    # cap an over-long prompt to a head+tail excerpt.
    if cfg["skip_lang_on_paste"] and _is_paste_dominant(prompt):
        cfg = {**cfg, "coach_language": False}
    prompt = _excerpt_prompt(prompt, cfg["max_prompt_chars"])
    try:
        analysis = gate_axes(analyze(prompt, cfg), cfg)
    except Exception as exc:
        sys.stderr.write("analysis failed: %r\n" % (exc,))
        return 1
    if not has_any_issues(analysis):
        print("[prompt-coach] No issues found — looks good.")
        return 0
    print(format_coaching(analysis, cfg))
    return 0


def main():
    # Control surface for the `/prompt-coach:*` command (toggle on/off, switch mode).
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
    if not _anything_to_coach(cfg):   # all axes off -> nothing to do, no model call
        sys.exit(0)

    prompt = event.get("prompt", "")
    if should_skip(prompt, cfg["min_chars"]):
        sys.exit(0)

    # When the prompt is mostly a pasted log/stack trace/code dump, correcting its
    # "English" is noise — suppress the language axis (keep prompt-quality
    # coaching). If that leaves nothing enabled, skip the model call entirely.
    if cfg["skip_lang_on_paste"] and _is_paste_dominant(prompt):
        cfg = {**cfg, "coach_language": False}
        if not _anything_to_coach(cfg):
            sys.exit(0)

    context = read_recent_context(
        event.get("transcript_path"),
        prompt,
        cfg["context_messages"],
        cfg["context_chars"],
        cfg["context_per_msg_chars"],
    )

    # Cap an over-long prompt to a head+tail excerpt so a huge paste can't blow
    # past the timeout. (Context echo-drop above used the full prompt.)
    prompt_for_model = _excerpt_prompt(prompt, cfg["max_prompt_chars"])

    try:
        analysis = analyze(prompt_for_model, cfg, context)
    except OllamaNotReady as exc:
        # Not an error: the local model just isn't up yet. Say so once (via the
        # display-only channel the agent never sees), then step aside.
        if cfg["debug"]:
            sys.stderr.write("prompt-coach error: %r\n" % (exc,))
        if exc.notice:
            sys.stdout.write(json.dumps({"systemMessage": exc.notice}))
        sys.exit(0)
    except Exception as exc:  # never break the user's workflow
        if cfg["debug"]:
            sys.stderr.write("prompt-coach error: %r\n" % (exc,))
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
