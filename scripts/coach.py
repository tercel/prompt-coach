#!/usr/bin/env python3
"""Prompt Dual-Coach — a Claude Code UserPromptSubmit hook.

On every prompt you submit, this hook asks a fast Claude model to analyze two
independent axes and feed coaching back to you:

  1. prompt  — the prompt as an instruction to a coding assistant
               (specificity, file paths, success criteria, constraints).
               Returns an improved prompt + one teaching tip.
  2. language — your expression in your chosen TARGET language (not just English).
               Returns concrete corrections (original -> fix + why) AND a fully
               rewritten, natural version.

This is the reusable "brain". The delivery shell here is a Claude Code hook;
the same analysis logic is meant to be reused later behind a terminal split-pane
or a floating panel.

Delivery modes (env COACH_MODE):
  - "annotate" (default): non-blocking. Injects the coaching as additionalContext
    so Claude shows it to you and answers the improved prompt.
  - "block": blocking. Surfaces the coaching and blocks the prompt so you
    consciously resubmit the improved version (a stricter learning loop).

Backends (env COACH_BACKEND):
  - "auto" (default): use the `claude` CLI if available (no pip, no separate API
    key — reuses your Claude Code auth), else fall back to the Anthropic SDK.
  - "cli": force the `claude` CLI.
  - "api": force the Anthropic Python SDK (needs `pip install anthropic` + key).

Configuration (environment variables):
  COACH_BACKEND            "auto" (default) | "cli" | "api"
  COACH_CLAUDE_BIN         path to the `claude` binary (default: found on PATH)
  COACH_CLI_FLAGS          extra space-separated flags for the `claude` CLI call
                           (e.g. "--bare") — for experimenting with faster boot
  ANTHROPIC_API_KEY        required only for the "api" backend / fallback
  COACH_TARGET_LANG        target language to coach (default: "English")
  COACH_NATIVE_LANG        your native language, used for explanations
                           (default: auto-detected from locale, e.g. LANG;
                            fallback "English")
  COACH_LEVEL              proficiency — tunes feedback depth. Free text;
                           recommended Beginner | Intermediate | Advanced
                           (or CEFR A1-C2). (default: "Advanced")
  COACH_MODEL              Claude model id (default: "claude-haiku-4-5")
  COACH_MODE               "annotate" (default) | "block"
  COACH_MIN_PROMPT_CHARS   skip prompts shorter than this (default: 12)
  COACH_CONTEXT_MESSAGES   recent turns of conversation context to include
                           (default: 6; set 0 to analyze the prompt in isolation)
  COACH_CONTEXT_CHARS      max characters of rendered context (default: 2000)
  COACH_TIMEOUT            backend timeout seconds (default: 25)
  COACH_DISABLE            set truthy to disable without uninstalling
  COACH_DEBUG              set truthy to print errors to stderr

Re-entrancy: the "cli" backend spawns `claude -p`, which itself fires
UserPromptSubmit and would re-invoke this hook. We set COACH_NESTED=1 on the
child so the nested invocation exits immediately — no recursion.

Design rule: this hook must NEVER break your workflow. Any error (missing
backend, network failure, bad JSON) results in a clean exit 0 with no output.
"""

import json
import os
import shutil
import sys

# ---------------------------------------------------------------------------
# Analysis contract (structured output schema enforced by the Messages API)
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

SYSTEM_TEMPLATE = (
    "You are a dual-axis writing coach embedded in a developer's AI coding "
    "assistant. The developer is a native {native} speaker practicing {target} "
    "at {level} level. You are given the recent CONVERSATION so far (it may be "
    "empty) and the user's NEW prompt. Analyze the NEW prompt on TWO independent "
    "axes and return ONLY the JSON object.\n\n"
    "1. language — evaluate the {target}-language expression of the NEW prompt.\n"
    "   - has_issues: true if grammar, word choice, or naturalness can be improved.\n"
    "   - corrections: specific fixes. Each: original (the exact problematic span), "
    "correction (the fixed span), explanation (one short clause written in {native}, "
    "explaining the rule).\n"
    "   - improved: a fully rewritten, natural {target} version of the NEW prompt, "
    "keeping the technical meaning identical.\n"
    "   If already native-quality, set has_issues=false, corrections=[], improved=\"\".\n\n"
    "2. prompt — evaluate the NEW prompt as the next instruction in THIS conversation.\n"
    "   - has_issues: true ONLY if, given the conversation context, the prompt is "
    "still genuinely ambiguous or under-specified. Do NOT flag information already "
    "established earlier (file paths, prior decisions, the task at hand): a short "
    "follow-up that is clear in context has NO issues.\n"
    "   - improved: a rewrite a coding assistant can act on precisely IN THIS "
    "context. It may rely on established context and need not restate it. Preserve "
    "the user's intent; never invent requirements they did not state.\n"
    "   - guidance: ONE short sentence written in {native} teaching the single most "
    "useful improvement.\n"
    "   If already strong, set has_issues=false, improved=\"\", guidance=\"\".\n\n"
    "Be concise. Never answer or execute the prompt — only analyze it."
)

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


def load_config(env):
    """Build a config dict from an environment-like mapping."""
    try:
        min_chars = int(env.get("COACH_MIN_PROMPT_CHARS", "12") or "12")
    except (TypeError, ValueError):
        min_chars = 12
    claude_bin = env.get("COACH_CLAUDE_BIN") or shutil.which(
        "claude", path=env.get("PATH")
    )
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
    return {
        "backend": (env.get("COACH_BACKEND", "auto") or "auto").strip().lower(),
        "claude_bin": claude_bin,
        "target": target,
        "native": native,
        "coach_language": coach_language,
        "level": env.get("COACH_LEVEL", "Advanced"),
        "model": env.get("COACH_MODEL", "claude-haiku-4-5"),
        "mode": (env.get("COACH_MODE", "annotate") or "annotate").strip().lower(),
        "min_chars": min_chars,
        "context_messages": _to_int(env.get("COACH_CONTEXT_MESSAGES"), 6),
        "context_chars": _to_int(env.get("COACH_CONTEXT_CHARS"), 2000),
        "timeout": _to_float(env.get("COACH_TIMEOUT"), 25.0),
        "disabled": _flag(env.get("COACH_DISABLE", "")),
        "debug": _flag(env.get("COACH_DEBUG", "")),
        "has_api_key": bool(env.get("ANTHROPIC_API_KEY")),
    }


def backend_available(cfg):
    """True if the configured backend can actually run."""
    backend = cfg["backend"]
    if backend == "cli":
        return bool(cfg["claude_bin"])
    if backend == "api":
        return bool(cfg["has_api_key"])
    return bool(cfg["claude_bin"] or cfg["has_api_key"])  # auto


def extract_json_text(text):
    """Pull the JSON object out of possibly fenced / chatty model output."""
    if not text or not text.strip():
        raise ValueError("empty model output")
    t = text.strip()
    if t.startswith("```"):
        t = t[3:]
        if t[:4].lower() == "json":
            t = t[4:]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    if not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            t = t[start:end + 1]
    return t


def should_skip(prompt, min_chars):
    """Cheap pre-filter so trivial / non-natural-language input is ignored."""
    if prompt is None:
        return True
    s = prompt.strip()
    if not s:
        return True
    if s.startswith("/"):   # slash command
        return True
    if s.startswith("!"):   # shell passthrough
        return True
    if len(s) < min_chars:
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
    """Instruction injected into Claude's context for annotate mode."""
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
    base = SYSTEM_TEMPLATE.format(
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


def gate_language(analysis, cfg):
    """Suppress the language axis when it doesn't apply (native == target)."""
    if cfg.get("coach_language", True):
        return analysis
    return {
        "language": {"has_issues": False, "corrections": [], "improved": ""},
        "prompt": analysis.get(
            "prompt", {"has_issues": False, "improved": "", "guidance": ""}
        ),
    }


def _analyze_cli(prompt, cfg, context=""):
    """Run analysis through the `claude` CLI (no SDK, no separate API key)."""
    import subprocess

    cmd = [
        cfg["claude_bin"], "-p",
        # Skip loading the user's MCP servers — they add large per-invocation boot
        # latency and this is a pure text-analysis call that needs no tools.
        "--strict-mcp-config",
        "--output-format", "json",
        "--model", cfg["model"],
        "--append-system-prompt", _system(cfg),
    ]
    # Optional escape hatch for experimenting with leaner-boot flags (e.g. --bare),
    # space-separated, without editing this file.
    extra = os.environ.get("COACH_CLI_FLAGS", "").split()
    if extra:
        cmd[2:2] = extra
    child_env = dict(os.environ)
    child_env["COACH_NESTED"] = "1"  # break hook recursion in the nested run
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
            "claude CLI exit %d: %s" % (proc.returncode, (proc.stderr or "").strip()[:200])
        )
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError("claude CLI returned an error result")
    return parse_analysis_text(extract_json_text(envelope.get("result", "")))


def _analyze_api(prompt, cfg, context=""):
    """Run analysis through the Anthropic Python SDK (needs ANTHROPIC_API_KEY)."""
    import anthropic  # lazy: pure-function tests must run without the SDK installed

    client = anthropic.Anthropic()
    resp = client.with_options(timeout=cfg["timeout"]).messages.create(  # type: ignore[call-overload]
        model=cfg["model"],
        max_tokens=1024,
        system=_system(cfg),
        messages=[{"role": "user", "content": _user_content(prompt, context)}],
        # output_config.format forces schema-valid JSON (GA; Anthropic structured outputs).
        output_config={"format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
    )
    text = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"), ""
    )
    return parse_analysis_text(extract_json_text(text))


def analyze(prompt, cfg, context=""):
    """Dispatch to the configured backend (auto = CLI first, API fallback)."""
    backend = cfg["backend"]
    if backend == "api":
        return _analyze_api(prompt, cfg, context)
    if backend == "cli":
        return _analyze_cli(prompt, cfg, context)
    # auto: prefer the CLI (zero-config), fall back to the API key if present
    if cfg["claude_bin"]:
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
            "No backend available: install the `claude` CLI or set ANTHROPIC_API_KEY.\n"
        )
        return 1
    try:
        analysis = gate_language(analyze(prompt, cfg), cfg)
    except Exception as exc:
        sys.stderr.write("analysis failed: %r\n" % (exc,))
        return 1
    if not has_any_issues(analysis):
        print("[prompt-dual-coach] No issues found — looks good.")
        return 0
    print(format_coaching(analysis, cfg))
    return 0


def main():
    # Local try-it mode (user-invoked), checked before the re-entrancy guard.
    if "--dry-run" in sys.argv[1:]:
        sys.exit(dry_run(sys.argv[1:]))

    # Re-entrancy guard: we are inside our own nested `claude -p` call.
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

    analysis = gate_language(analysis, cfg)
    out, err, code = build_delivery(analysis, cfg)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    sys.exit(code)


if __name__ == "__main__":
    main()
