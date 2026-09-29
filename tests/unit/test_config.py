import tempfile
import unittest
from pathlib import Path

from repo_check.config import CONFIG_FILENAME, default_config, load_config, parse_config
from repo_check.errors import ConfigError


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, text: str, name: str = CONFIG_FILENAME) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path


class DefaultConfigTests(ConfigTestCase):
    def test_missing_file_uses_defaults(self):
        config = load_config(self.root)
        self.assertEqual(config.exclude, ())
        self.assertIsNone(config.source)
        rule = config.rule("FILE_TOO_LARGE")
        self.assertTrue(rule.enabled)
        self.assertEqual(dict(rule.options), {"max_lines": 1000})

    def test_empty_file_uses_defaults(self):
        path = self.write_config("")
        config = load_config(self.root)
        self.assertEqual(config.source, path)
        self.assertEqual(config.rules, default_config().rules)


class RuleDefaultTests(unittest.TestCase):
    def test_broken_local_doc_link_is_disabled_by_default(self):
        self.assertFalse(default_config().rule("BROKEN_LOCAL_DOC_LINK").enabled)

    def test_every_other_rule_is_enabled_by_default(self):
        rules = default_config().rules
        self.assertEqual(len(rules), 14)
        self.assertEqual(
            sorted(rule_id for rule_id, rule in rules.items() if rule.enabled),
            sorted(set(rules) - {"BROKEN_LOCAL_DOC_LINK"}),
        )

    def test_config_without_the_rule_keeps_it_disabled(self):
        config = parse_config({"repo_check": {"rules": {"TODO_COMMENT": {"enabled": True}}}})
        self.assertFalse(config.rule("BROKEN_LOCAL_DOC_LINK").enabled)
        self.assertEqual(config.rules, default_config().rules)

    def test_explicit_enable_and_disable(self):
        for value in (True, False):
            with self.subTest(enabled=value):
                config = parse_config(
                    {"repo_check": {"rules": {"BROKEN_LOCAL_DOC_LINK": {"enabled": value}}}}
                )
                self.assertIs(config.rule("BROKEN_LOCAL_DOC_LINK").enabled, value)
                self.assertEqual(dict(config.rule("BROKEN_LOCAL_DOC_LINK").options), {})

    def test_strict_validation_is_unchanged_for_the_rule(self):
        for table in ({"enabled": "true"}, {"enabled": 1}, {"max_lines": 5}):
            with self.subTest(table=table):
                with self.assertRaises(ConfigError):
                    parse_config({"repo_check": {"rules": {"BROKEN_LOCAL_DOC_LINK": table}}})


class ValidConfigTests(ConfigTestCase):
    def test_repository_local_file(self):
        path = self.write_config(
            '[repo_check]\nexclude = ["vendor/", "./gen/*.py"]\n\n'
            "[repo_check.rules.FILE_TOO_LARGE]\nenabled = true\nmax_lines = 250\n"
        )
        config = load_config(self.root)
        self.assertEqual(config.source, path)
        self.assertEqual(config.exclude, ("vendor/", "gen/*.py"))
        self.assertTrue(config.rule("FILE_TOO_LARGE").enabled)
        self.assertEqual(config.rule("FILE_TOO_LARGE").options["max_lines"], 250)

    def test_threshold_override_only(self):
        self.write_config("[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 1\n")
        rule = load_config(self.root).rule("FILE_TOO_LARGE")
        self.assertTrue(rule.enabled)
        self.assertEqual(rule.options["max_lines"], 1)

    def test_rule_disabled(self):
        self.write_config("[repo_check.rules.FILE_TOO_LARGE]\nenabled = false\n")
        rule = load_config(self.root).rule("FILE_TOO_LARGE")
        self.assertFalse(rule.enabled)
        self.assertEqual(rule.options["max_lines"], 1000)

    def test_explicit_path_overrides_repository_file(self):
        self.write_config("[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 10\n")
        explicit = self.write_config(
            "[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 20\n", name="other.toml"
        )
        config = load_config(self.root, explicit)
        self.assertEqual(config.source, explicit)
        self.assertEqual(config.rule("FILE_TOO_LARGE").options["max_lines"], 20)

    def test_explicit_path_must_exist(self):
        with self.assertRaisesRegex(ConfigError, "config file not found"):
            load_config(self.root, self.root / "missing.toml")

    def test_explicit_path_that_is_not_a_file_is_not_reported_as_missing(self):
        (self.root / "conf.d").mkdir()
        with self.assertRaisesRegex(ConfigError, "config path is not a regular file: "):
            load_config(self.root, self.root / "conf.d")


class InvalidConfigTests(ConfigTestCase):
    def assert_invalid(self, text: str, pattern: str):
        self.write_config(text)
        with self.assertRaisesRegex(ConfigError, pattern):
            load_config(self.root)

    def test_malformed_toml(self):
        self.assert_invalid("[repo_check\nexclude = ", "malformed TOML")

    def test_invalid_utf8(self):
        (self.root / CONFIG_FILENAME).write_bytes(b"[repo_check]\nexclude = ['\xff']\n")
        with self.assertRaisesRegex(ConfigError, "malformed TOML|not valid UTF-8"):
            load_config(self.root)

    def test_max_lines_wrong_type(self):
        self.assert_invalid(
            '[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = "1000"\n',
            r"FILE_TOO_LARGE\.max_lines must be a positive integer, got '1000'",
        )

    def test_max_lines_float_is_not_coerced(self):
        self.assert_invalid(
            "[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 10.0\n", "max_lines must be"
        )

    def test_max_lines_bool_is_not_coerced(self):
        self.assert_invalid(
            "[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = true\n", "max_lines must be"
        )

    def test_max_lines_non_positive(self):
        self.assert_invalid("[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = 0\n", "max_lines")
        self.assert_invalid("[repo_check.rules.FILE_TOO_LARGE]\nmax_lines = -5\n", "max_lines")

    def test_enabled_wrong_type(self):
        self.assert_invalid(
            '[repo_check.rules.FILE_TOO_LARGE]\nenabled = "no"\n',
            r"FILE_TOO_LARGE\.enabled must be a boolean",
        )

    def test_unknown_rule_id(self):
        self.assert_invalid("[repo_check.rules.FILE_TOO_BIG]\nenabled = false\n", "FILE_TOO_BIG")

    def test_unknown_rule_option(self):
        self.assert_invalid("[repo_check.rules.FILE_TOO_LARGE]\nmax_line = 5\n", "max_line")

    def test_unknown_repo_check_key(self):
        self.assert_invalid('[repo_check]\nexclusions = ["x"]\n', "exclusions")

    def test_unknown_top_level_key(self):
        self.assert_invalid("[rules.FILE_TOO_LARGE]\nmax_lines = 5\n", "unknown top-level")

    def test_exclude_must_be_array_of_strings(self):
        self.assert_invalid('[repo_check]\nexclude = "vendor"\n', "array of strings")
        self.assert_invalid("[repo_check]\nexclude = [1]\n", "non-empty strings")
        self.assert_invalid('[repo_check]\nexclude = [""]\n', "non-empty strings")

    def test_exclude_rejects_absolute_and_backslash_patterns(self):
        self.assert_invalid('[repo_check]\nexclude = ["/etc"]\n', "relative")
        self.assert_invalid("[repo_check]\nexclude = ['a\\b']\n", "relative")

    def test_parse_config_rejects_non_table_rule(self):
        with self.assertRaisesRegex(ConfigError, "must be a table"):
            parse_config({"repo_check": {"rules": {"FILE_TOO_LARGE": 5}}})


if __name__ == "__main__":
    unittest.main()
