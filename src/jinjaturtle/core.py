from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from .handlers import (
    BaseHandler,
    IniHandler,
    JsonHandler,
    TomlHandler,
    YamlHandler,
    XmlHandler,
)


class QuotedString(str):
    """Marker type for strings that must be double-quoted in YAML output."""

    pass


def _fallback_str_representer(dumper: yaml.SafeDumper, data: Any):
    """
    Fallback for objects the dumper doesn't know about. Represent them as
    plain strings.
    """
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data))


class _TurtleDumper(yaml.SafeDumper):
    """Custom YAML dumper that always double-quotes QuotedString values."""

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


def make_var_name(role_prefix: str, path: Iterable[str]) -> str:
    """Wrapper for :meth:`BaseHandler.make_var_name`.

    This keeps the public API (and tests) working while the implementation
    lives on the BaseHandler class.
    """
    return BaseHandler.make_var_name(role_prefix, path)


def detect_format(path: Path, explicit: str | None = None) -> str:
    """
    Determine config format (toml, yaml, json, ini-ish, xml) from argument or filename.
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
    return fmt, parsed


def flatten_config(fmt: str, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
    """
    Flatten parsed config into a list of (path_tuple, value).
    """
    handler = _HANDLERS.get(fmt)
    if handler is None:
        # preserve previous ValueError for unsupported formats
        raise ValueError(f"Unsupported format: {fmt}")
    return handler.flatten(parsed)


def _normalize_default_value(value: Any) -> Any:
    """
    Ensure that 'true' / 'false' end up as quoted strings in YAML, not booleans.

    - bool -> QuotedString("true"/"false")
    - "true"/"false" (any case) -> QuotedString(original_text)
    - everything else -> unchanged
    """
    if isinstance(value, bool):
        # YAML booleans are lower-case; we keep them as strings.
        return QuotedString("true" if value else "false")
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return QuotedString(value)
    return value


def generate_defaults_yaml(
    role_prefix: str,
    flat_items: list[tuple[tuple[str, ...], Any]],
) -> str:
    """
    Create YAML for defaults/main.yml from flattened items.

    Boolean/boolean-like values ("true"/"false") are forced to be *strings*
    and double-quoted in the resulting YAML so that Ansible does not coerce
    them back into Python booleans.
    """
    defaults: dict[str, Any] = {}
    for path, value in flat_items:
        var_name = make_var_name(role_prefix, path)
        defaults[var_name] = _normalize_default_value(value)

    return yaml.dump(
        defaults,
        Dumper=_TurtleDumper,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        explicit_start=True,
        indent=2,
    )


def generate_template(
    fmt: str,
    parsed: Any,
    role_prefix: str,
    original_text: str | None = None,
) -> str:
    """
    Generate a Jinja2 template for the config.

    If original_text is provided, comments and blank lines are preserved by
    patching values in-place. Otherwise we fall back to reconstructing from
    the parsed structure (no comments). JSON of course does not support
    comments.
    """
    handler = _HANDLERS.get(fmt)
    if handler is None:
        raise ValueError(f"Unsupported format: {fmt}")
    return handler.generate_template(parsed, role_prefix, original_text=original_text)
