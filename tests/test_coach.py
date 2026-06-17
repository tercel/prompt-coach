"""Unit tests for the Prompt Coach brain.

These cover the pure helpers only (no network, no SDK). Run with:

    python3 -m unittest discover -s tests -t .
or  python3 tests/test_coach.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import coach  # type: ignore[import-not-found]  # noqa: E402  (resolved at runtime via sys.path)


# Point "~" at an empty temp home for the whole module so the default state path
# (~/.claude/prompt-coach-state.json) never reads the real user's file. Tests that
# set COACH_STATE_DIR explicitly still override this.
_FAKE_HOME = ""
_HOME_PATCHER = None


def _fake_expanduser(p):
    return p.replace("~", _FAKE_HOME, 1) if p == "~" or p.startswith("~/") else p


def setUpModule():
    global _FAKE_HOME, _HOME_PATCHER
    _FAKE_HOME = tempfile.mkdtemp(prefix="prompt-coach-test-home-")
    _HOME_PATCHER = mock.patch("os.path.expanduser", side_effect=_fake_expanduser)
    _HOME_PATCHER.start()


def tearDownModule():
    if _HOME_PATCHER is not None:
        _HOME_PATCHER.stop()
    if _FAKE_HOME:
        shutil.rmtree(_FAKE_HOME, ignore_errors=True)


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
        self.assertTrue(coach.should_skip("/help", 6))

    def test_shell_passthrough(self):
        self.assertTrue(coach.should_skip("!ls -la", 6))

    def test_empty(self):
        self.assertTrue(coach.should_skip("   ", 6))

    def test_none(self):
        self.assertTrue(coach.should_skip(None, 6))

    def test_normal_prompt(self):
        self.assertFalse(
            coach.should_skip("please refactor the auth module to use JWT", 6)
        )

    def test_short_vague_multiword_is_coached(self):
        # The whole point: short but genuinely vague requests must NOT be skipped.
        for p in ("fix bug", "review code", "add tests", "add auth", "make it better"):
            self.assertFalse(coach.should_skip(p, 6), p)

    def test_single_token_is_skipped(self):
        for p in ("yes", "ok", "continue", "commit", "optimize", "refactor", "build"):
            self.assertTrue(coach.should_skip(p, 6), p)

    def test_bare_answers_and_numbers_skipped(self):
        for p in ("Yes.", "no!", "1", "2", "  OK  ", "lgtm"):
            self.assertTrue(coach.should_skip(p, 6), p)

    def test_context_rich_phrases_skipped(self):
        for p in ("build it", "test it", "run tests", "commit and push", "do it"):
            self.assertTrue(coach.should_skip(p, 6), p)

    def test_dev_command_lines_skipped(self):
        for p in ("git commit -m 'fix'", "npm install react", "docker build .",
                  "cargo test --all"):
            self.assertTrue(coach.should_skip(p, 6), p)

    def test_ambiguous_prefixes_not_treated_as_commands(self):
        # "make"/"go" are English words too — these must be coached, not skipped.
        for p in ("make it better", "go implement the login feature"):
            self.assertFalse(coach.should_skip(p, 6), p)

    def test_ultra_short_multiword_floor(self):
        self.assertTrue(coach.should_skip("a b", 6))      # junk below floor
        self.assertTrue(coach.should_skip("go on", 6))    # 5 chars < 6


class TestLangModeAndState(unittest.TestCase):
    def _env(self, tmpdir, **extra):
        env = {"PATH": "", "COACH_STATE_DIR": tmpdir}
        env.update(extra)
        return env

    def test_defaults_correct_on_translate_off(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(self._env(d))
            self.assertTrue(cfg["correct_on"])
            self.assertFalse(cfg["translate_on"])
            self.assertEqual(cfg["lang_mode"], "correct")
            self.assertTrue(cfg["axis_language"])

    def test_translate_only_derives_translate_mode(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(
                self._env(d, COACH_CORRECT="off", COACH_TRANSLATE="on")
            )
            self.assertEqual(cfg["lang_mode"], "translate")

    def test_both_on_derives_auto_mode(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(self._env(d, COACH_TRANSLATE="on"))
            self.assertTrue(cfg["correct_on"])
            self.assertTrue(cfg["translate_on"])
            self.assertEqual(cfg["lang_mode"], "auto")

    def test_both_off_disables_language_axis(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(self._env(d, COACH_CORRECT="off"))
            self.assertFalse(cfg["axis_language"])

    def test_load_state_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(coach.load_state(self._env(d)), {})

    def test_state_scope_global_is_shared(self):
        with tempfile.TemporaryDirectory() as d:
            a = coach.state_path(self._env(d, CLAUDE_PROJECT_DIR="/proj/a"))
            b = coach.state_path(self._env(d, CLAUDE_PROJECT_DIR="/proj/b"))
            self.assertEqual(a, b)  # global: project dir ignored

    def test_state_scope_project_isolates(self):
        with tempfile.TemporaryDirectory() as d:
            a = coach.state_path(
                self._env(d, COACH_STATE_SCOPE="project", CLAUDE_PROJECT_DIR="/proj/a")
            )
            b = coach.state_path(
                self._env(d, COACH_STATE_SCOPE="project", CLAUDE_PROJECT_DIR="/proj/b")
            )
            self.assertNotEqual(a, b)
            # Same project → same path (hook and command must agree).
            a2 = coach.state_path(
                self._env(d, COACH_STATE_SCOPE="project", CLAUDE_PROJECT_DIR="/proj/a")
            )
            self.assertEqual(a, a2)

    def test_project_scope_toggle_does_not_leak_across_projects(self):
        with tempfile.TemporaryDirectory() as d:
            proj_a = self._env(d, COACH_STATE_SCOPE="project", CLAUDE_PROJECT_DIR="/p/a")
            proj_b = self._env(d, COACH_STATE_SCOPE="project", CLAUDE_PROJECT_DIR="/p/b")
            coach._control(["--ctl", "enable", "translate"], proj_a)
            self.assertTrue(coach.load_config(proj_a)["translate_on"])
            self.assertFalse(coach.load_config(proj_b)["translate_on"])

    def test_control_switch_state_wins_over_env(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d, COACH_TRANSLATE="off")
            self.assertEqual(coach._control(["--ctl", "enable", "translate"], env), 0)
            self.assertTrue(coach.load_config(env)["translate_on"])

    def test_power_off_then_on(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            coach._control(["--ctl", "power", "off"], env)
            self.assertTrue(coach.load_config(env)["disabled"])
            coach._control(["--ctl", "power", "on"], env)
            self.assertFalse(coach.load_config(env)["disabled"])

    def test_bare_on_off_not_accepted(self):
        # The master switch is `power on/off`; bare on/off is intentionally not
        # a command (avoids "is /coach on power, or all features?" ambiguity).
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(coach._control(["--ctl", "on"], self._env(d)), 2)
            self.assertEqual(coach._control(["--ctl", "off"], self._env(d)), 2)

    def test_help_prints_usage_and_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            for word in ("help", "-h", "--help"):
                self.assertEqual(coach._control(["--ctl", word], env), 0)
            # help never writes the state file
            self.assertFalse(os.path.exists(coach.state_path(env)))

    def test_power_on_overrides_env_disable(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d, COACH_DISABLE="1")
            coach._control(["--ctl", "power", "on"], env)
            self.assertFalse(coach.load_config(env)["disabled"])

    def test_control_bad_action_returns_2(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(coach._control(["--ctl", "bogus"], self._env(d)), 2)

    def test_features_default_on(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(self._env(d))
            self.assertTrue(cfg["evaluate_on"])
            self.assertTrue(cfg["correct_on"])

    def test_disable_evaluate_independently(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            self.assertEqual(coach._control(["--ctl", "disable", "evaluate"], env), 0)
            cfg = coach.load_config(env)
            self.assertFalse(cfg["evaluate_on"])
            self.assertTrue(cfg["axis_language"])  # language untouched

    def test_enable_disable_correct_and_translate_independent(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            coach._control(["--ctl", "enable", "translate"], env)
            coach._control(["--ctl", "disable", "correct"], env)
            cfg = coach.load_config(env)
            self.assertFalse(cfg["correct_on"])
            self.assertTrue(cfg["translate_on"])
            self.assertEqual(cfg["lang_mode"], "translate")
            self.assertTrue(cfg["evaluate_on"])  # evaluate untouched

    def test_enable_multiple_features_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            self.assertEqual(
                coach._control(["--ctl", "enable", "correct", "translate"], env), 0
            )
            cfg = coach.load_config(env)
            self.assertTrue(cfg["correct_on"])
            self.assertTrue(cfg["translate_on"])
            self.assertEqual(cfg["lang_mode"], "auto")

    def test_disable_multiple_with_comma_and_hyphen(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            self.assertEqual(
                coach._control(["--ctl", "disable", "correct,translate"], env), 0
            )
            cfg = coach.load_config(env)
            self.assertFalse(cfg["axis_language"])
            # hyphenated master: "power-off"
            self.assertEqual(coach._control(["--ctl", "power-off"], env), 0)
            self.assertTrue(coach.load_config(env)["disabled"])

    def test_enable_requires_a_feature(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(coach._control(["--ctl", "enable"], self._env(d)), 2)

    def test_enable_unknown_feature_returns_2(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                coach._control(["--ctl", "enable", "bogus"], self._env(d)), 2
            )

    def test_feature_abbreviations(self):
        self.assertEqual(coach._resolve_feature("e"), "evaluate")
        self.assertEqual(coach._resolve_feature("c"), "correct")
        self.assertEqual(coach._resolve_feature("t"), "translate")
        self.assertEqual(coach._resolve_feature("translate"), "translate")
        self.assertEqual(coach._resolve_feature("EVALUATE"), "evaluate")
        # Only single letters / full names — multi-letter prefixes are not aliases.
        self.assertIsNone(coach._resolve_feature("ev"))
        self.assertIsNone(coach._resolve_feature("en"))
        self.assertIsNone(coach._resolve_feature("x"))

    def test_control_enable_with_letters(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            self.assertEqual(coach._control(["--ctl", "enable", "c", "t"], env), 0)
            cfg = coach.load_config(env)
            self.assertTrue(cfg["correct_on"])
            self.assertTrue(cfg["translate_on"])
            self.assertEqual(coach._control(["--ctl", "disable", "e"], env), 0)
            self.assertFalse(coach.load_config(env)["evaluate_on"])

    def test_control_enable_unknown_token_errors(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(coach._control(["--ctl", "enable", "en"], self._env(d)), 2)

    def test_help_language(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            for lang in ([], ["en"]):
                self.assertEqual(coach._control(["--ctl", "help", *lang], env), 0)
            self.assertEqual(coach._control(["--ctl", "help", "zh"], env), 0)
        self.assertIn("指令", coach._CTL_USAGE_ZH)
        self.assertNotIn("指令", coach._CTL_USAGE)

    def test_control_lang_sets_native_and_target(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            self.assertEqual(
                coach._control(["--ctl", "lang", "native", "Chinese", "target", "English"], env),
                0,
            )
            cfg = coach.load_config(env)
            self.assertEqual(cfg["native"], "Chinese")   # case preserved
            self.assertEqual(cfg["target"], "English")

    def test_lang_accepts_codes_and_aliases(self):
        self.assertEqual(coach.normalize_language("zh"), "Chinese")
        self.assertEqual(coach.normalize_language("en"), "English")
        self.assertEqual(coach.normalize_language("ja"), "Japanese")
        self.assertEqual(coach.normalize_language("jp"), "Japanese")   # alias
        self.assertEqual(coach.normalize_language("kr"), "Korean")
        self.assertEqual(coach.normalize_language("english"), "English")  # case
        self.assertEqual(coach.normalize_language("Swahili"), "Swahili")  # passthrough

    def test_control_lang_with_codes(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            coach._control(["--ctl", "lang", "native", "zh", "target", "en"], env)
            cfg = coach.load_config(env)
            self.assertEqual(cfg["native"], "Chinese")
            self.assertEqual(cfg["target"], "English")

    def test_control_lang_single_field(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            coach._control(["--ctl", "lang", "native", "Japanese"], env)
            self.assertEqual(coach.load_config(env)["native"], "Japanese")
            self.assertEqual(coach.load_config(env)["target"], "English")  # untouched

    def test_lang_state_overrides_env(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d, COACH_NATIVE_LANG="French")
            coach._control(["--ctl", "lang", "native", "Korean"], env)
            self.assertEqual(coach.load_config(env)["native"], "Korean")

    def test_lang_native_equals_target_suppresses_language_axis(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            coach._control(["--ctl", "lang", "native", "English", "target", "English"], env)
            self.assertFalse(coach.load_config(env)["coach_language"])

    def test_control_lang_requires_valid_keys(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(coach._control(["--ctl", "lang"], self._env(d)), 2)
            self.assertEqual(
                coach._control(["--ctl", "lang", "foo", "bar"], self._env(d)), 2
            )

    def test_help_states_letter_and_full_name(self):
        # Help must make clear both the full name and the single letter work.
        for usage in (coach._CTL_USAGE, coach._CTL_USAGE_ZH):
            self.assertIn("enable c t", usage)
            self.assertIn("enable correct translate", usage)
            for letter in ("= e", "= c", "= t"):
                self.assertIn(letter, usage)

    def test_env_feature_override(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(self._env(d, COACH_EVALUATE="off"))
            self.assertFalse(cfg["evaluate_on"])

    def test_gate_axes_drops_prompt_when_off(self):
        cfg = {"coach_language": True, "axis_language": True, "evaluate_on": False}
        gated = coach.gate_axes(make_analysis(True, True), cfg)
        self.assertFalse(gated["prompt"]["has_issues"])      # prompt zeroed
        self.assertTrue(gated["language"]["has_issues"])     # language kept

    def test_gate_axes_drops_language_when_off(self):
        cfg = {"coach_language": True, "axis_language": False, "evaluate_on": True}
        gated = coach.gate_axes(make_analysis(True, True), cfg)
        self.assertFalse(gated["language"]["has_issues"])
        self.assertTrue(gated["prompt"]["has_issues"])

    def test_gate_axes_identity_when_all_on(self):
        cfg = {"coach_language": True, "axis_language": True, "evaluate_on": True}
        analysis = make_analysis(True, True)
        self.assertIs(coach.gate_axes(analysis, cfg), analysis)

    def test_control_status_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d)
            self.assertEqual(coach._control(["--ctl", "status"], env), 0)
            self.assertFalse(os.path.exists(coach.state_path(env)))

    def test_system_prompt_translate_mode(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(
                self._env(
                    d, COACH_CORRECT="off", COACH_TRANSLATE="on",
                    COACH_NATIVE_LANG="Chinese",
                )
            )
            sysp = coach._system(cfg)
            self.assertIn("render it", sysp)
            self.assertIn("Chinese", sysp)

    def test_system_prompt_correct_mode_is_default(self):
        with tempfile.TemporaryDirectory() as d:
            sysp = coach._system(coach.load_config(self._env(d)))
            self.assertIn("evaluate the English-language expression", sysp)

    def test_format_coaching_translate_label(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = coach.load_config(
                self._env(d, COACH_CORRECT="off", COACH_TRANSLATE="on")
            )
            block = coach.format_coaching(make_analysis(), cfg)
            self.assertIn("[English]", block)
            self.assertNotIn("[Language: English]", block)


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
        cfg = coach.load_config({"OPENAI_API_KEY": "x"})
        block = coach.format_coaching(make_analysis(), cfg)
        self.assertIn("Fix the 401 error in src/auth/login.ts", block)
        self.assertIn("I have a bug", block)
        self.assertIn("subject-verb agreement", block)


class TestBuildDelivery(unittest.TestCase):
    def test_silent_when_clean(self):
        cfg = coach.load_config({"OPENAI_API_KEY": "x"})
        out, err, code = coach.build_delivery(make_analysis(False, False), cfg)
        self.assertEqual((out, err, code), ("", "", 0))

    def test_annotate_mode(self):
        cfg = coach.load_config({"OPENAI_API_KEY": "x"})  # default mode = annotate
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
        cfg = coach.load_config({"OPENAI_API_KEY": "x", "COACH_MODE": "block"})
        out, err, code = coach.build_delivery(make_analysis(), cfg)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("I have a bug", err)


class TestLoadConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = coach.load_config({})  # no locale env -> native falls back to English
        self.assertEqual(cfg["target"], "English")
        self.assertEqual(cfg["native"], "English")
        self.assertEqual(cfg["platform"], "codex")
        self.assertEqual(cfg["api_model"], "gpt-4o-mini")
        self.assertEqual(cfg["cli_model"], "")
        self.assertEqual(cfg["anthropic_model"], "claude-haiku-4-5-20251001")
        self.assertEqual(cfg["mode"], "annotate")
        self.assertEqual(cfg["backend"], "auto")
        self.assertFalse(cfg["has_api_key"])
        self.assertFalse(cfg["has_anthropic_key"])

    def test_detects_hook_platform(self):
        self.assertEqual(
            coach.load_config({"CLAUDE_PLUGIN_ROOT": "/plugin"})["platform"], "claude"
        )
        self.assertEqual(
            coach.load_config({"PLUGIN_ROOT": "/plugin"})["platform"], "codex"
        )

    def test_codex_sets_both_roots_detects_codex(self):
        # Codex injects BOTH PLUGIN_ROOT and CLAUDE_PLUGIN_ROOT (the latter for
        # claude-ecosystem compat). PLUGIN_ROOT must win so we don't misroute
        # a Codex session to the Claude backend.
        cfg = coach.load_config(
            {"PLUGIN_ROOT": "/plugin", "CLAUDE_PLUGIN_ROOT": "/plugin"}
        )
        self.assertEqual(cfg["platform"], "codex")

    def test_explicit_platform_overrides_detection(self):
        cfg = coach.load_config(
            {"COACH_PLATFORM": "claude", "PLUGIN_ROOT": "/codex-plugin"}
        )
        self.assertEqual(cfg["platform"], "claude")

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
                "COACH_CODEX_BIN": "/usr/local/bin/codex",
                "COACH_CLAUDE_BIN": "/usr/local/bin/claude",
                "COACH_MODEL": "gpt-5.5",
                "OPENAI_API_KEY": "x",
                "ANTHROPIC_API_KEY": "y",
            }
        )
        self.assertEqual(cfg["target"], "Portuguese")
        self.assertEqual(cfg["mode"], "block")
        self.assertEqual(cfg["backend"], "cli")
        self.assertEqual(cfg["min_chars"], 20)
        self.assertEqual(cfg["codex_bin"], "/usr/local/bin/codex")
        self.assertEqual(cfg["claude_bin"], "/usr/local/bin/claude")
        self.assertEqual(cfg["cli_model"], "gpt-5.5")
        self.assertEqual(cfg["api_model"], "gpt-5.5")
        self.assertEqual(cfg["anthropic_model"], "gpt-5.5")
        self.assertTrue(cfg["has_api_key"])
        self.assertTrue(cfg["has_anthropic_key"])

    def test_min_chars_default(self):
        self.assertEqual(coach.load_config({})["min_chars"], 6)

    def test_bad_min_chars_falls_back(self):
        cfg = coach.load_config({"COACH_MIN_PROMPT_CHARS": "abc"})
        self.assertEqual(cfg["min_chars"], 6)

    def test_bad_timeout_falls_back(self):
        cfg = coach.load_config({"COACH_TIMEOUT": "soon"})
        self.assertEqual(cfg["timeout"], 25.0)


class TestBackendAvailable(unittest.TestCase):
    def _cfg(self, **env):
        # PATH="" keeps shutil.which from finding a real `codex` on this machine
        env.setdefault("PATH", "")
        return coach.load_config(env)

    def test_cli_needs_binary(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_BACKEND="cli", COACH_CODEX_BIN="/bin/codex")))
        self.assertFalse(coach.backend_available(self._cfg(COACH_BACKEND="cli")))

    def test_api_needs_key(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_BACKEND="api", OPENAI_API_KEY="x")))
        self.assertFalse(coach.backend_available(self._cfg(COACH_BACKEND="api")))

    def test_auto_accepts_either(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_CODEX_BIN="/bin/codex")))
        self.assertTrue(coach.backend_available(
            self._cfg(OPENAI_API_KEY="x")))
        self.assertFalse(coach.backend_available(self._cfg()))

    def test_claude_platform_uses_claude_ecosystem(self):
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_PLATFORM="claude", COACH_CLAUDE_BIN="/bin/claude")))
        self.assertTrue(coach.backend_available(
            self._cfg(COACH_PLATFORM="claude", ANTHROPIC_API_KEY="x")))
        self.assertFalse(coach.backend_available(
            self._cfg(COACH_PLATFORM="claude", COACH_CODEX_BIN="/bin/codex")))


class TestAnalyzeDispatch(unittest.TestCase):
    def test_auto_prefers_codex_cli(self):
        cfg = coach.load_config(
            {"PATH": "", "COACH_CODEX_BIN": "/bin/codex", "OPENAI_API_KEY": "x"}
        )
        expected = make_analysis()
        with mock.patch.object(coach, "_analyze_cli", return_value=expected) as cli:
            with mock.patch.object(coach, "_analyze_api") as api:
                self.assertIs(coach.analyze("prompt", cfg), expected)
        cli.assert_called_once()
        api.assert_not_called()

    def test_auto_falls_back_to_openai_api(self):
        cfg = coach.load_config(
            {"PATH": "", "COACH_CODEX_BIN": "/bin/codex", "OPENAI_API_KEY": "x"}
        )
        expected = make_analysis()
        with mock.patch.object(coach, "_analyze_cli", side_effect=RuntimeError("failed")):
            with mock.patch.object(coach, "_analyze_api", return_value=expected) as api:
                self.assertIs(coach.analyze("prompt", cfg), expected)
        api.assert_called_once()

    def test_auto_prefers_claude_cli_in_claude_hook(self):
        cfg = coach.load_config(
            {
                "PATH": "",
                "COACH_PLATFORM": "claude",
                "COACH_CLAUDE_BIN": "/bin/claude",
                "ANTHROPIC_API_KEY": "x",
            }
        )
        expected = make_analysis()
        with mock.patch.object(coach, "_analyze_claude_cli", return_value=expected) as cli:
            with mock.patch.object(coach, "_analyze_anthropic_api") as api:
                self.assertIs(coach.analyze("prompt", cfg), expected)
        cli.assert_called_once()
        api.assert_not_called()

    def test_claude_auto_falls_back_to_anthropic_api(self):
        cfg = coach.load_config(
            {
                "PATH": "",
                "COACH_PLATFORM": "claude",
                "COACH_CLAUDE_BIN": "/bin/claude",
                "ANTHROPIC_API_KEY": "x",
            }
        )
        expected = make_analysis()
        with mock.patch.object(
            coach, "_analyze_claude_cli", side_effect=RuntimeError("failed")
        ):
            with mock.patch.object(
                coach, "_analyze_anthropic_api", return_value=expected
            ) as api:
                self.assertIs(coach.analyze("prompt", cfg), expected)
        api.assert_called_once()


class TestCodexCliBackend(unittest.TestCase):
    def _run_writing_last_message(self, message):
        """subprocess.run stub that writes `message` to --output-last-message."""

        def _side_effect(cmd, *_a, **_kw):  # noqa
            path = cmd[cmd.index("--output-last-message") + 1]
            with open(path, "w", encoding="utf-8") as out:
                out.write(message)
            return mock.Mock(returncode=0, stdout="", stderr="")

        return _side_effect

    def test_uses_ephemeral_structured_output_without_hooks(self):
        cfg = coach.load_config(
            {"PATH": "", "COACH_CODEX_BIN": "/bin/codex", "COACH_MODEL": "gpt-5-mini"}
        )
        side_effect = self._run_writing_last_message(make_analysis_text())
        with mock.patch("subprocess.run", side_effect=side_effect) as run:
            coach._analyze_cli("fix login", cfg)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], ["/bin/codex", "exec"])
        self.assertIn("--ephemeral", cmd)
        self.assertIn("--ignore-user-config", cmd)
        self.assertIn("--output-schema", cmd)
        self.assertIn("--output-last-message", cmd)
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(run.call_args.kwargs["env"]["COACH_NESTED"], "1")

    def test_parses_final_message_file_not_stdout(self):
        # The final-message file is authoritative; chatty stdout (with stray
        # braces) must not corrupt parsing.
        cfg = coach.load_config({"PATH": "", "COACH_CODEX_BIN": "/bin/codex"})

        def _side_effect(cmd, *_a, **_kw):  # noqa
            path = cmd[cmd.index("--output-last-message") + 1]
            with open(path, "w", encoding="utf-8") as out:
                out.write(make_analysis_text())
            return mock.Mock(
                returncode=0, stdout="thinking… {garbage: not json}", stderr=""
            )

        with mock.patch("subprocess.run", side_effect=_side_effect):
            analysis = coach._analyze_cli("fix login", cfg)
        self.assertFalse(analysis["prompt"]["has_issues"])

    def test_falls_back_to_stdout_when_no_final_message(self):
        cfg = coach.load_config({"PATH": "", "COACH_CODEX_BIN": "/bin/codex"})
        completed = mock.Mock(returncode=0, stdout=make_analysis_text(), stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            analysis = coach._analyze_cli("fix login", cfg)
        self.assertFalse(analysis["prompt"]["has_issues"])


class TestClaudeCliBackend(unittest.TestCase):
    def test_uses_claude_print_mode_and_disables_mcp(self):
        cfg = coach.load_config(
            {
                "PATH": "",
                "COACH_PLATFORM": "claude",
                "COACH_CLAUDE_BIN": "/bin/claude",
            }
        )
        envelope = json.dumps({"result": make_analysis_text(), "is_error": False})
        completed = mock.Mock(returncode=0, stdout=envelope, stderr="")
        with mock.patch("subprocess.run", return_value=completed) as run:
            coach._analyze_claude_cli("fix login", cfg)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], ["/bin/claude", "-p"])
        self.assertIn("--strict-mcp-config", cmd)
        self.assertIn("--append-system-prompt", cmd)
        self.assertEqual(run.call_args.kwargs["env"]["COACH_NESTED"], "1")


class TestOpenAiApiBackend(unittest.TestCase):
    def test_uses_responses_api_with_structured_output(self):
        cfg = coach.load_config(
            {"PATH": "", "OPENAI_API_KEY": "x", "COACH_API_MODEL": "gpt-test"}
        )
        response = mock.Mock(output_text=make_analysis_text())
        client = mock.Mock()
        client.responses.create.return_value = response
        openai_module = mock.Mock()
        openai_module.OpenAI.return_value = client
        with mock.patch.dict(sys.modules, {"openai": openai_module}):
            analysis = coach._analyze_api("fix login", cfg)
        self.assertFalse(analysis["prompt"]["has_issues"])
        call = client.responses.create.call_args.kwargs
        self.assertEqual(call["model"], "gpt-test")
        self.assertIn("dual-axis writing coach", call["instructions"])
        self.assertEqual(call["text"]["format"]["type"], "json_schema")
        self.assertTrue(call["text"]["format"]["strict"])


class TestAnthropicApiBackend(unittest.TestCase):
    def test_uses_messages_api_with_structured_output(self):
        cfg = coach.load_config(
            {
                "PATH": "",
                "COACH_PLATFORM": "claude",
                "ANTHROPIC_API_KEY": "x",
                "COACH_ANTHROPIC_MODEL": "claude-test",
            }
        )
        tool_input = json.loads(make_analysis_text())
        response = mock.Mock(
            content=[mock.Mock(type="tool_use", input=tool_input)]
        )
        client = mock.Mock()
        client.with_options.return_value.messages.create.return_value = response
        anthropic_module = mock.Mock()
        anthropic_module.Anthropic.return_value = client
        with mock.patch.dict(sys.modules, {"anthropic": anthropic_module}):
            analysis = coach._analyze_anthropic_api("fix login", cfg)
        self.assertFalse(analysis["prompt"]["has_issues"])
        call = client.with_options.return_value.messages.create.call_args.kwargs
        self.assertEqual(call["model"], "claude-test")
        # Structured output via tool-use (the supported Messages-API mechanism).
        self.assertEqual(call["tool_choice"]["name"], "prompt_coach_analysis")
        self.assertEqual(call["tools"][0]["input_schema"], coach.ANALYSIS_SCHEMA)


def make_analysis_text():
    return json.dumps(
        {
            "language": {"has_issues": False, "corrections": [], "improved": ""},
            "prompt": {"has_issues": False, "improved": "", "guidance": ""},
        }
    )


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
