from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import datetime
import yaml

from .loop_analyzer import LoopAnalyzer, LoopCandidate
from .handlers import (
    BaseHandler,
    IniHandler,
    JsonHandler,
    TomlHandler,
    YamlHandler,
    XmlHandler,
)


class QuotedString(str):
    """
    Marker type for strings that must be double-quoted in YAML output.
    """

    pass


def _fallback_str_representer(dumper: yaml.SafeDumper, data: Any):
    """
    Fallback for objects the dumper doesn't know about.
    """
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data))


class _TurtleDumper(yaml.SafeDumper):
    """
    Custom YAML dumper that always double-quotes QuotedString values.
    """

    pass


def _quoted_str_representer(dumper: yaml.SafeDumper, data: QuotedString):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_TurtleDumper.add_representer(QuotedString, _quoted_str_representer)
# Use our fallback for any unknown object types
_TurtleDumper.add_representer(None, _fallback_str_representer)

_HANDLERS: dict[str, BaseHandler] = {}

_INI_HANDLER = IniHandler()
_JSON_HANDLER = JsonHandler()
_TOML_HANDLER = TomlHandler()
_YAML_HANDLER = YamlHandler()
_XML_HANDLER = XmlHandler()

_HANDLERS["ini"] = _INI_HANDLER
_HANDLERS["json"] = _JSON_HANDLER
_HANDLERS["toml"] = _TOML_HANDLER
_HANDLERS["yaml"] = _YAML_HANDLER
_HANDLERS["xml"] = _XML_HANDLER


def get_supported_formats() -> set[str]:
    """Return the set of supported input formats."""
    return set(_HANDLERS.keys())


def dump_yaml(data: Any, *, sort_keys: bool = True) -> str:
    """Dump YAML using JinjaTurtle's dumper settings.

    This is used by both the single-file and multi-file code paths.
    """
    return yaml.dump(
        data,
        Dumper=_TurtleDumper,
        sort_keys=sort_keys,
        default_flow_style=False,
        allow_unicode=True,
        explicit_start=True,
        indent=2,
    )


def make_var_name(role_prefix: str, path: Iterable[str]) -> str:
    """
    Wrapper for :meth:`BaseHandler.make_var_name`.
    """
    return BaseHandler.make_var_name(role_prefix, path)


def detect_format(path: Path, explicit: str | None = None) -> str:
    """
    Determine config format from argument or filename.
    """
    if explicit:
        return explicit
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix == ".toml":
        return "toml"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix in {".ini", ".cfg", ".conf"} or name.endswith(".ini"):
        return "ini"
    if suffix == ".xml":
        return "xml"
    # Fallback: treat as INI-ish
    return "ini"


def parse_config(path: Path, fmt: str | None = None) -> tuple[str, Any]:
    """
    Parse config file into a Python object.
    """
    fmt = detect_format(path, fmt)
    handler = _HANDLERS.get(fmt)
    if handler is None:
        raise ValueError(f"Unsupported config format: {fmt}")
    parsed = handler.parse(path)
    # Make sure datetime objects are treated as strings (TOML, YAML)
    parsed = _stringify_timestamps(parsed)

    return fmt, parsed


def analyze_loops(fmt: str, parsed: Any) -> list[LoopCandidate]:
    """
    Analyze parsed config to find loop opportunities.
    """
    analyzer = LoopAnalyzer()
    candidates = analyzer.analyze(parsed, fmt)

    # Filter by confidence threshold
    return [c for c in candidates if c.confidence >= LoopAnalyzer.MIN_CONFIDENCE]


def flatten_config(
    fmt: str, parsed: Any, loop_candidates: list[LoopCandidate] | None = None
) -> list[tuple[tuple[str, ...], Any]]:
    """
    Flatten parsed config into (path, value) pairs.

    If loop_candidates is provided, paths within those loops are excluded
    from flattening (they'll be handled via loops in the template).
    """
    handler = _HANDLERS.get(fmt)
    if handler is None:
        raise ValueError(f"Unsupported format: {fmt}")

    all_items = handler.flatten(parsed)

    if not loop_candidates:
        return all_items

    # Build set of paths to exclude (anything under a loop path)
    excluded_prefixes = {candidate.path for candidate in loop_candidates}

    # Filter out items that fall under loop paths
    filtered_items = []
    for item_path, value in all_items:
        # Check if this path starts with any loop path
        is_excluded = False
        for loop_path in excluded_prefixes:
            if _path_starts_with(item_path, loop_path):
                is_excluded = True
                break

        if not is_excluded:
            filtered_items.append((item_path, value))

    return filtered_items


def _path_starts_with(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    """Check if path starts with prefix."""
    if len(path) < len(prefix):
        return False
    return path[: len(prefix)] == prefix


def generate_ansible_yaml(
    role_prefix: str,
    flat_items: list[tuple[tuple[str, ...], Any]],
    loop_candidates: list[LoopCandidate] | None = None,
) -> str:
    """
    Create Ansible YAML for defaults/main.yml.
    """
    defaults: dict[str, Any] = {}

    # Add scalar variables
    for path, value in flat_items:
        var_name = make_var_name(role_prefix, path)
        defaults[var_name] = value  # No normalization - keep original types

    # Add loop collections
    if loop_candidates:
        for candidate in loop_candidates:
            var_name = make_var_name(role_prefix, candidate.path)
            defaults[var_name] = candidate.items

    return dump_yaml(defaults, sort_keys=True)


def generate_jinja2_template(
    fmt: str,
    parsed: Any,
    role_prefix: str,
    original_text: str | None = None,
    loop_candidates: list[LoopCandidate] | None = None,
) -> str:
    """
    Generate a Jinja2 template for the config.
    """
    handler = _HANDLERS.get(fmt)

    if handler is None:
        raise ValueError(f"Unsupported format: {fmt}")

    # Check if handler supports loop-aware generation
    if hasattr(handler, "generate_jinja2_template_with_loops") and loop_candidates:
        return handler.generate_jinja2_template_with_loops(
            parsed, role_prefix, original_text, loop_candidates
        )

    # Fallback to original scalar-only generation
    return handler.generate_jinja2_template(
        parsed, role_prefix, original_text=original_text
    )


def _stringify_timestamps(obj: Any) -> Any:
    """
    Recursively walk a parsed config and turn any datetime/date/time objects
    into plain strings in ISO-8601 form.

    This prevents Python datetime objects from leaking into YAML/Jinja, which
    would otherwise reformat the value (e.g. replacing 'T' with a space).

    This commonly occurs otherwise with TOML and YAML files, which sees
    Python automatically convert those sorts of strings into datetime objects.
    """
    if isinstance(obj, dict):
        return {k: _stringify_timestamps(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_timestamps(v) for v in obj]

    # TOML & YAML both use the standard datetime types
    if isinstance(obj, datetime.datetime):
        # Use default ISO-8601: 'YYYY-MM-DDTHH:MM:SS±HH:MM' (with 'T')
        return obj.isoformat()
    if isinstance(obj, (datetime.date, datetime.time)):
        return obj.isoformat()

    return obj
