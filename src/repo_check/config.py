"""Configuration loading and validation.

Configuration comes from built-in defaults, optionally overridden by a TOML
file: either ``repo-check.toml`` at the repository root or an explicit
``--config`` path. Only the ``[repo_check]`` table is recognized::

    [repo_check]
    exclude = ["vendor/", "*.min.js"]

    [repo_check.rules.FILE_TOO_LARGE]
    enabled = true
    max_lines = 1000

    [repo_check.rules.TRACKED_BUILD_ARTIFACT]
    enabled = true

``PYTHON_FUNCTION_TOO_LARGE`` (default 100) and ``PYTHON_CLASS_TOO_LARGE``
(default 500) also accept ``max_lines``; ``LARGE_CHANGESET`` accepts
``max_files`` (default 50). ``FREQUENTLY_CHANGED_FILE`` accepts
``min_touching_commits`` (default 10), ``min_touch_percent`` (default 10, from
1 to 100), and ``min_scanned_commits`` (default 50). Rules without options
(BROKEN_LOCAL_DOC_LINK, PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE, PYTHON_BARE_EXCEPT,
PYTHON_MUTABLE_DEFAULT, SENSITIVE_PROJECT_FILE_CHANGED, SWIFT_FORCE_CAST,
SWIFT_FORCE_TRY, TODO_COMMENT, TRACKED_BUILD_ARTIFACT) accept only
``enabled``. Every rule is enabled by default except BROKEN_LOCAL_DOC_LINK.

Validation is strict: unknown keys, unknown rule IDs, and values of the wrong
type raise ``ConfigError`` instead of being ignored or coerced.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from repo_check.errors import ConfigError

CONFIG_FILENAME = "repo-check.toml"

OptionValue = int | str | bool


@dataclass(frozen=True)
class RuleConfig:
    """Effective settings for one rule."""

    enabled: bool
    options: Mapping[str, OptionValue]


@dataclass(frozen=True)
class Config:
    """Effective configuration after defaults and file overrides are merged.

    ``exclude`` holds normalized exclusion patterns (see ``discovery``).
    ``source`` is the config file that was loaded, or None for defaults.
    """

    exclude: tuple[str, ...] = ()
    rules: Mapping[str, RuleConfig] = field(default_factory=dict)
    source: Path | None = None

    def rule(self, rule_id: str) -> RuleConfig:
        return self.rules[rule_id]


def _positive_int(value: Any) -> bool:
    # bool is a subclass of int; reject it explicitly.
    return type(value) is int and value >= 1


def _percent(value: Any) -> bool:
    return _positive_int(value) and value <= 100


@dataclass(frozen=True)
class _OptionSpec:
    default: OptionValue
    is_valid: Callable[[Any], bool]
    expected: str


# Known rule options and their defaults. Adding a rule with options means
# adding an explicit entry here.
_RULE_OPTIONS: Mapping[str, Mapping[str, _OptionSpec]] = {
    "BROKEN_LOCAL_DOC_LINK": {},
    "FILE_TOO_LARGE": {
        "max_lines": _OptionSpec(1000, _positive_int, "a positive integer"),
    },
    "FREQUENTLY_CHANGED_FILE": {
        "min_touching_commits": _OptionSpec(10, _positive_int, "a positive integer"),
        "min_touch_percent": _OptionSpec(10, _percent, "an integer from 1 to 100"),
        "min_scanned_commits": _OptionSpec(50, _positive_int, "a positive integer"),
    },
    "LARGE_CHANGESET": {
        "max_files": _OptionSpec(50, _positive_int, "a positive integer"),
    },
    "PRODUCTION_CHANGE_WITHOUT_TEST_CHANGE": {},
    "PYTHON_BARE_EXCEPT": {},
    "PYTHON_CLASS_TOO_LARGE": {
        "max_lines": _OptionSpec(500, _positive_int, "a positive integer"),
    },
    "PYTHON_FUNCTION_TOO_LARGE": {
        "max_lines": _OptionSpec(100, _positive_int, "a positive integer"),
    },
    "PYTHON_MUTABLE_DEFAULT": {},
    "SENSITIVE_PROJECT_FILE_CHANGED": {},
    "SWIFT_FORCE_CAST": {},
    "SWIFT_FORCE_TRY": {},
    "TODO_COMMENT": {},
    "TRACKED_BUILD_ARTIFACT": {},
}

# Rules that are available but disabled unless a config enables them. Every
# other rule is enabled by default. BROKEN_LOCAL_DOC_LINK resolves links as
# file paths, which does not match the link semantics of documentation built
# by static-site generators.
_DISABLED_BY_DEFAULT = frozenset({"BROKEN_LOCAL_DOC_LINK"})

_TOP_LEVEL_KEYS = {"exclude", "rules"}


def default_config() -> Config:
    rules = {
        rule_id: RuleConfig(
            enabled=rule_id not in _DISABLED_BY_DEFAULT,
            options=MappingProxyType({name: spec.default for name, spec in specs.items()}),
        )
        for rule_id, specs in _RULE_OPTIONS.items()
    }
    return Config(exclude=(), rules=MappingProxyType(rules), source=None)


def load_config(root: Path, explicit_path: Path | None = None) -> Config:
    """Load the effective configuration for a repository.

    An explicit path must exist. Otherwise ``<root>/repo-check.toml`` is used
    if present, and built-in defaults if not.
    """
    if explicit_path is not None:
        if not explicit_path.exists():
            raise ConfigError(f"config file not found: {explicit_path}")
        if not explicit_path.is_file():
            raise ConfigError(f"config path is not a regular file: {explicit_path}")
        return _load_file(explicit_path)

    candidate = root / CONFIG_FILENAME
    if candidate.is_file():
        return _load_file(candidate)
    return default_config()


def _load_file(path: Path) -> Config:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: malformed TOML: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: config file is not valid UTF-8") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read config file: {exc.strerror}") from exc
    return parse_config(data, source=path)


def parse_config(data: Mapping[str, Any], source: Path | None = None) -> Config:
    """Validate parsed TOML data and merge it over the defaults."""
    where = str(source) if source is not None else "config"

    unknown = sorted(set(data) - {"repo_check"})
    if unknown:
        raise ConfigError(
            f"{where}: unknown top-level key(s): {', '.join(unknown)}; "
            "settings must be under [repo_check]"
        )

    section = data.get("repo_check", {})
    if not isinstance(section, dict):
        raise ConfigError(f"{where}: [repo_check] must be a table")

    unknown = sorted(set(section) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) in [repo_check]: {', '.join(unknown)}")

    exclude = _parse_exclude(section.get("exclude", []), where)
    rules = _parse_rules(section.get("rules", {}), where)
    return Config(exclude=exclude, rules=rules, source=source)


def _parse_exclude(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{where}: repo_check.exclude must be an array of strings")
    patterns = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(
                f"{where}: repo_check.exclude entries must be non-empty strings, got {item!r}"
            )
        pattern = item.strip()
        if pattern.startswith("/") or "\\" in pattern:
            raise ConfigError(
                f"{where}: repo_check.exclude entry {item!r} must be a relative "
                "POSIX-style pattern (no leading '/' or backslashes)"
            )
        while pattern.startswith("./"):
            pattern = pattern[2:]
        if not pattern or pattern == "/":
            raise ConfigError(f"{where}: repo_check.exclude entry {item!r} is empty")
        patterns.append(pattern)
    return tuple(patterns)


def _parse_rules(value: Any, where: str) -> Mapping[str, RuleConfig]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: repo_check.rules must be a table")

    unknown = sorted(set(value) - set(_RULE_OPTIONS))
    if unknown:
        raise ConfigError(f"{where}: unknown rule ID(s) in repo_check.rules: {', '.join(unknown)}")

    defaults = default_config().rules
    rules: dict[str, RuleConfig] = {}
    for rule_id, specs in _RULE_OPTIONS.items():
        table = value.get(rule_id, {})
        prefix = f"repo_check.rules.{rule_id}"
        if not isinstance(table, dict):
            raise ConfigError(f"{where}: {prefix} must be a table")

        unknown = sorted(set(table) - set(specs) - {"enabled"})
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) in {prefix}: {', '.join(unknown)}")

        enabled = table.get("enabled", defaults[rule_id].enabled)
        if type(enabled) is not bool:
            raise ConfigError(f"{where}: {prefix}.enabled must be a boolean, got {enabled!r}")

        options: dict[str, OptionValue] = {}
        for name, spec in specs.items():
            option = table.get(name, spec.default)
            if not spec.is_valid(option):
                raise ConfigError(
                    f"{where}: {prefix}.{name} must be {spec.expected}, got {option!r}"
                )
            options[name] = option

        rules[rule_id] = RuleConfig(enabled=enabled, options=MappingProxyType(options))
    return MappingProxyType(rules)
