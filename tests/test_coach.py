"""Unit tests for the Prompt Dual-Coach brain.

These cover the pure helpers only (no network, no SDK). Run with:

    python3 -m unittest discover -s tests -t .
or  python3 tests/test_coach.py
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import coach  # type: ignore[import-not-found]  # noqa: E402  (resolved at runtime via sys.path)


def make_analysis(lang_issues=True, prompt_issues=True):
    text = json.dumps(
        {
            "language": {
                "has_issues": lang_issues,
                "corrections": [
                    {
                        "original": "i has a bug",
                        "correction": "I have a bug",
                        "explanation": "subject-verb agreement",
                    }
                ],
                "improved": "I have a bug in the login flow.",
            },
            "prompt": {
                "has_issues": prompt_issues,
                "improved": "Fix the 401 error in src/auth/login.ts",
                "guidance": "add the exact file path",
            },
        }
    )
    return coach.parse_analysis_text(text)


class TestShouldSkip(unittest.TestCase):
    def test_slash_command(self):
        self.assertTrue(coach.should_skip("/help", 12))

    def test_shell_passthrough(self):
        self.assertTrue(coach.should_skip("!ls -la", 12))

    def test_empty(self):
        self.assertTrue(coach.should_skip("   ", 12))

    def test_none(self):
        self.assertTrue(coach.should_skip(None, 12))

    def test_too_short(self):
        self.assertTrue(coach.should_skip("fix it", 12))

    def test_normal_prompt(self):
        self.assertFalse(
            coach.should_skip("please refactor the auth module to use JWT", 12)
        )


class TestParse(unittest.TestCase):
    def test_valid(self):
        a = make_analysis()
        self.assertTrue(a["language"]["has_issues"])
        self.assertEqual(a["language"]["improved"], "I have a bug in the login flow.")
        self.assertEqual(a["prompt"]["improved"], "Fix the 401 error in src/auth/login.ts")

    def test_missing_fields_are_defaulted(self):
        a = coach.parse_analysis_text(json.dumps({}))
        self.assertFalse(a["language"]["has_issues"])
        self.assertEqual(a["language"]["corrections"], [])
        self.assertEqual(a["prompt"]["guidance"], "")

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            coach.parse_analysis_text("not json")


class TestHasAnyIssues(unittest.TestCase):
    def test_true_when_either(self):
        self.assertTrue(coach.has_any_issues(make_analysis(True, False)))
        self.assertTrue(coach.has_any_issues(make_analysis(False, True)))

    def test_false_when_clean(self):
        self.assertFalse(coach.has_any_issues(make_analysis(False, False)))


class TestFormatCoaching(unittest.TestCase):
    def test_contains_both_axes(self):
        cfg = coach.load_config({"ANTHROPIC_API_KEY": "x"})
        block = coach.format_coaching(make_analysis(), cfg)
        self.assertIn("Fix the 401 error in src/auth/login.ts", block)
        self.assertIn("I have a bug", block)
        self.assertIn("subject-verb agreement", block)


class TestBuildDelivery(unittest.TestCase):
    def test_silent_when_clean(self):
        cfg = coach.load_config({"ANTHROPIC_API_KEY": "x"})
        out, err, code = coach.build_delivery(make_analysis(False, False), cfg)
        self.assertEqual((out, err, code), ("", "", 0))

    def test_annotate_mode(self):
        cfg = coach.load_config({"ANTHROPIC_API_KEY": "x"})  # default mode = annotate
        out, err, code = coach.build_delivery(make_analysis(), cfg)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit"
        )
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Fix the 401 error in src/auth/login.ts", ctx)

    def test_block_mode(self):
        cfg = coach.load_config({"ANTHROPIC_API_KEY": "x", "COACH_MODE": "block"})
        out, err, code = coach.build_delivery(make_analysis(), cfg)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("I have a bug", err)


class TestLoadConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = coach.load_config({})  # no locale env -> native falls back to English
        self.assertEqual(cfg["target"], "English")
        self.assertEqual(cfg["native"], "English")
        self.assertEqual(cfg["model"], "claude-haiku-4-5")
        self.assertEqual(cfg["mode"], "annotate")
        self.assertEqual(cfg["backend"], "auto")
        self.assertFalse(cfg["has_api_key"])

    def test_native_autodetected_from_locale(self):
        self.assertEqual(coach.load_config({"LANG": "zh_CN.UTF-8"})["native"], "Chinese")

    def test_native_env_overrides_locale(self):
        cfg = coach.load_config({"COACH_NATIVE_LANG": "Korean", "LANG": "zh_CN.UTF-8"})
        self.assertEqual(cfg["native"], "Korean")

    def test_overrides(self):
        cfg = coach.load_config(
            {
                "COACH_TARGET_LANG": "Portuguese",
                "COACH_NATIVE_LANG": "English",
                "COACH_MODE": "BLOCK",
                "COACH_BACKEND": "CLI",
                "COACH_MIN_PROMPT_CHARS": "20",
                "COACH_CLAUDE_BIN": "/usr/local/bin/claude",
                "ANTHROPIC_API_KEY": "x",
            }
        )
        self.assertEqual(cfg["target"], "Portuguese")
        self.assertEqual(cfg["mode"], "block")
        self.assertEqual(cfg["backend"], "cli")
        self.assertEqual(cfg["min_chars"], 20)
        self.assertEqual(cfg["claude_bin"], "/usr/local/bin/claude")
        self.assertTrue(cfg["has_api_key"])

    def test_bad_min_chars_falls_back(self):
        cfg = coach.load_config({"COACH_MIN_PROMPT_CHARS": "abc"})
        self.assertEqual(cfg["min_chars"], 12)

    def test_bad_timeout_falls_back(self):
        cfg = coach.load_config({"COACH_TIMEOUT": "soon"})
        self.assertEqual(cfg["timeout"], 25.0)


class TestBackendAvailable(unittest.TestCase):
    def _cfg(self, **env):
        # PATH="" keeps shutil.which from finding a real `claude` on this machine
        env.setdefault("PATH", "")
        return coach.load_config(env)

    def test_cli_needs_binary(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_BACKEND="cli", COACH_CLAUDE_BIN="/bin/claude")))
        self.assertFalse(coach.backend_available(self._cfg(COACH_BACKEND="cli")))

    def test_api_needs_key(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_BACKEND="api", ANTHROPIC_API_KEY="x")))
        self.assertFalse(coach.backend_available(self._cfg(COACH_BACKEND="api")))

    def test_auto_accepts_either(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_CLAUDE_BIN="/bin/claude")))
        self.assertTrue(coach.backend_available(
            self._cfg(ANTHROPIC_API_KEY="x")))
        self.assertFalse(coach.backend_available(self._cfg()))


class TestDetectNativeLanguage(unittest.TestCase):
    def test_lang(self):
        self.assertEqual(coach.detect_native_language({"LANG": "zh_CN.UTF-8"}), "Chinese")

    def test_lc_all_priority(self):
        self.assertEqual(
            coach.detect_native_language({"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}),
            "Japanese",
        )

    def test_language_list(self):
        self.assertEqual(coach.detect_native_language({"LANGUAGE": "pt_BR:pt"}), "Portuguese")

    def test_unknown_or_c_locale_falls_back(self):
        self.assertEqual(coach.detect_native_language({"LANG": "C"}), "English")

    def test_none_falls_back(self):
        self.assertEqual(coach.detect_native_language({}), "English")

    def test_custom_default(self):
        self.assertEqual(coach.detect_native_language({}, default="Spanish"), "Spanish")


class TestDryRunPrompt(unittest.TestCase):
    def test_args_preferred(self):
        self.assertEqual(
            coach._dry_run_prompt(["--dry-run", "hello", "world"], "ignored"),
            "hello world",
        )

    def test_stdin_fallback(self):
        self.assertEqual(coach._dry_run_prompt(["--dry-run"], "piped text\n"), "piped text")

    def test_empty(self):
        self.assertEqual(coach._dry_run_prompt(["--dry-run"], ""), "")


class TestLanguageGate(unittest.TestCase):
    def test_explicit_equal_disables(self):
        cfg = coach.load_config(
            {"COACH_NATIVE_LANG": "English", "COACH_TARGET_LANG": "English"}
        )
        self.assertFalse(cfg["coach_language"])

    def test_explicit_equal_case_insensitive(self):
        cfg = coach.load_config(
            {"COACH_NATIVE_LANG": "english ", "COACH_TARGET_LANG": "English"}
        )
        self.assertFalse(cfg["coach_language"])

    def test_explicit_different_enabled(self):
        cfg = coach.load_config(
            {"COACH_NATIVE_LANG": "Chinese", "COACH_TARGET_LANG": "English"}
        )
        self.assertTrue(cfg["coach_language"])

    def test_autodetected_equal_keeps_enabled(self):
        # native auto-detected English == target English, but NOT explicit -> stay on
        cfg = coach.load_config({"LANG": "en_US.UTF-8", "COACH_TARGET_LANG": "English"})
        self.assertTrue(cfg["coach_language"])

    def test_default_enabled(self):
        self.assertTrue(coach.load_config({})["coach_language"])

    def test_gate_zeros_language_axis(self):
        cfg = coach.load_config(
            {"COACH_NATIVE_LANG": "English", "COACH_TARGET_LANG": "English"}
        )
        gated = coach.gate_language(make_analysis(True, True), cfg)
        self.assertFalse(gated["language"]["has_issues"])
        self.assertEqual(gated["language"]["corrections"], [])
        self.assertEqual(gated["language"]["improved"], "")
        self.assertTrue(gated["prompt"]["has_issues"])  # prompt axis preserved

    def test_gate_noop_when_enabled(self):
        cfg = coach.load_config(
            {"COACH_NATIVE_LANG": "Chinese", "COACH_TARGET_LANG": "English"}
        )
        analysis = make_analysis(True, True)
        self.assertIs(coach.gate_language(analysis, cfg), analysis)


class TestExtractJsonText(unittest.TestCase):
    OBJ = '{"language":{"has_issues":false,"corrections":[],"improved":""},' \
          '"prompt":{"has_issues":false,"improved":"","guidance":""}}'

    def test_plain(self):
        self.assertEqual(json.loads(coach.extract_json_text(self.OBJ))["prompt"]["has_issues"], False)

    def test_json_fenced(self):
        fenced = "```json\n" + self.OBJ + "\n```"
        self.assertIn("language", json.loads(coach.extract_json_text(fenced)))

    def test_bare_fenced(self):
        fenced = "```\n" + self.OBJ + "\n```"
        self.assertIn("language", json.loads(coach.extract_json_text(fenced)))

    def test_chatty_prefix(self):
        chatty = "Sure, here is the analysis:\n" + self.OBJ + "\nHope this helps!"
        self.assertIn("prompt", json.loads(coach.extract_json_text(chatty)))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            coach.extract_json_text("   ")


class TestExtractTextFromContent(unittest.TestCase):
    def test_string(self):
        self.assertEqual(coach.extract_text_from_content("hello"), "hello")

    def test_block_list_keeps_text_only(self):
        content = [
            {"type": "text", "text": "a"},
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "b"},
        ]
        self.assertEqual(coach.extract_text_from_content(content), "a\nb")

    def test_none(self):
        self.assertEqual(coach.extract_text_from_content(None), "")


class TestExtractMessages(unittest.TestCase):
    LINES = [
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": "Refactor auth to JWT in src/auth/login.ts"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "text", "text": "Done. I updated login.ts."}]}}),
        # tool result -> no text block -> skipped
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}),
        "{ broken json",                                   # skipped
        json.dumps({"type": "summary", "summary": "..."}),  # no role -> skipped
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": "now do the same for logout"}}),
    ]

    def test_parses_only_text_turns(self):
        msgs = coach.extract_messages_from_lines(self.LINES)
        self.assertEqual(
            msgs,
            [
                ("user", "Refactor auth to JWT in src/auth/login.ts"),
                ("assistant", "Done. I updated login.ts."),
                ("user", "now do the same for logout"),
            ],
        )


class TestBuildContext(unittest.TestCase):
    def test_drops_trailing_prompt_echo(self):
        msgs = [
            ("user", "Refactor auth to JWT in src/auth/login.ts"),
            ("assistant", "Done."),
            ("user", "now do the same for logout"),
        ]
        out = coach.build_context(msgs, "now do the same for logout", 6, 2000)
        self.assertIn("Refactor auth to JWT", out)
        self.assertNotIn("now do the same for logout", out)

    def test_max_messages(self):
        msgs = [
            ("user", "MSG_A"), ("assistant", "MSG_B"),
            ("user", "MSG_C"), ("assistant", "MSG_D"),
        ]
        out = coach.build_context(msgs, "", 2, 2000)
        self.assertNotIn("MSG_A", out)
        self.assertNotIn("MSG_B", out)
        self.assertIn("MSG_C", out)
        self.assertIn("MSG_D", out)

    def test_max_chars_truncates_tail(self):
        msgs = [("user", "x" * 500)]
        out = coach.build_context(msgs, "", 6, 50)
        self.assertTrue(out.startswith("…"))
        self.assertLessEqual(len(out), 51)


class TestUserContent(unittest.TestCase):
    def test_includes_context(self):
        uc = coach._user_content("do X", "User: earlier\nAssistant: ok")
        self.assertIn("<conversation_so_far>", uc)
        self.assertIn("earlier", uc)
        self.assertIn("do X", uc)

    def test_omits_empty_context(self):
        uc = coach._user_content("do X", "")
        self.assertNotIn("<conversation_so_far>", uc)
        self.assertIn("do X", uc)


class TestContextConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = coach.load_config({})
        self.assertEqual(cfg["context_messages"], 6)
        self.assertEqual(cfg["context_chars"], 2000)

    def test_overrides(self):
        cfg = coach.load_config(
            {"COACH_CONTEXT_MESSAGES": "10", "COACH_CONTEXT_CHARS": "500"}
        )
        self.assertEqual(cfg["context_messages"], 10)
        self.assertEqual(cfg["context_chars"], 500)

    def test_bad_values_fall_back(self):
        cfg = coach.load_config({"COACH_CONTEXT_MESSAGES": "lots"})
        self.assertEqual(cfg["context_messages"], 6)


if __name__ == "__main__":
    unittest.main()
