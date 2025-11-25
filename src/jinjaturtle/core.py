from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any, Iterable

import yaml

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = None  # type: ignore


def detect_format(path: Path, explicit: str | None = None) -> str:
    """
    Determine config format (toml vs ini-ish) from argument or filename.
    """
    if explicit:
        return explicit
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix == ".toml":
        return "toml"
    if suffix in {".ini", ".cfg", ".conf"} or name.endswith(".ini"):
        return "ini"
    # Fallback: treat as INI-ish
    return "ini"


def parse_config(path: Path, fmt: str | None = None) -> tuple[str, Any]:
    """
    Parse config file into a Python object:

      TOML -> nested dict
      INI  -> configparser.ConfigParser
    """
    fmt = detect_format(path, fmt)

    if fmt == "toml":
        if tomllib is None:
            raise RuntimeError(
                "tomllib/tomli is required to parse TOML files but is not installed"
            )
        with path.open("rb") as f:
            data = tomllib.load(f)
        return fmt, data

    if fmt == "ini":
        parser = configparser.ConfigParser()
        parser.optionxform = str  # preserve key case
        with path.open("r", encoding="utf-8") as f:
            parser.read_file(f)
        return fmt, parser

    raise ValueError(f"Unsupported config format: {fmt}")


def flatten_config(fmt: str, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
    """
    Flatten parsed config into a list of (path_tuple, value).

    Examples:
      TOML:  [server.tls] enabled = true
        -> (("server", "tls", "enabled"), True)

      INI: [somesection] foo = "bar"
        -> (("somesection", "foo"), "bar")

    For INI, values are processed as strings (quotes stripped when obvious).
    """
    items: list[tuple[tuple[str, ...], Any]] = []

    if fmt == "toml":

        def _walk(obj: Any, path: tuple[str, ...] = ()) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, path + (str(k),))
            else:
                items.append((path, obj))

        _walk(parsed)

    elif fmt == "ini":
        parser: configparser.ConfigParser = parsed
        for section in parser.sections():
            for key, value in parser.items(section, raw=True):
                raw = value.strip()
                # Strip surrounding quotes from INI values for defaults
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
                    processed: Any = raw[1:-1]
                else:
                    processed = raw
                items.append(((section, key), processed))
    else:  # pragma: no cover
        raise ValueError(f"Unsupported format: {fmt}")

    return items


def make_var_name(role_prefix: str, path: Iterable[str]) -> str:
    """
    Build an Ansible var name like:
      role_prefix_section_subsection_key

    Sanitises parts to lowercase [a-z0-9_] and strips extras.
    """
    role_prefix = role_prefix.strip().lower()
    clean_parts: list[str] = []

    for part in path:
        part = str(part).strip()
        part = part.replace(" ", "_")
        cleaned_chars: list[str] = []
        for c in part:
            if c.isalnum() or c == "_":
                cleaned_chars.append(c.lower())
            else:
                cleaned_chars.append("_")
        cleaned_part = "".join(cleaned_chars).strip("_")
        if cleaned_part:
            clean_parts.append(cleaned_part)

    if clean_parts:
        return role_prefix + "_" + "_".join(clean_parts)
    return role_prefix


def generate_defaults_yaml(
    role_prefix: str, flat_items: list[tuple[tuple[str, ...], Any]]
) -> str:
    """
    Create YAML for defaults/main.yml from flattened items.
    """
    defaults: dict[str, Any] = {}
    for path, value in flat_items:
        var_name = make_var_name(role_prefix, path)
        defaults[var_name] = value

    return yaml.safe_dump(
        defaults,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def _generate_toml_template(role_prefix: str, data: dict[str, Any]) -> str:
    """
    Generate a TOML Jinja2 template from parsed TOML dict.

    Values become Jinja placeholders, with quoting preserved for strings:
      foo = "bar" -> foo = "{{ prefix_foo }}"
      port = 8080 -> port = {{ prefix_port }}
    """
    lines: list[str] = []

    def emit_kv(path: tuple[str, ...], key: str, value: Any) -> None:
        var_name = make_var_name(role_prefix, path + (key,))
        if isinstance(value, str):
            lines.append(f'{key} = "{{{{ {var_name} }}}}"')
        else:
            lines.append(f"{key} = {{{{ {var_name} }}}}")

    def walk(obj: dict[str, Any], path: tuple[str, ...] = ()) -> None:
        scalar_items = {k: v for k, v in obj.items() if not isinstance(v, dict)}
        nested_items = {k: v for k, v in obj.items() if isinstance(v, dict)}

        if path:
            header = ".".join(path)
            lines.append(f"[{header}]")

        for key, val in scalar_items.items():
            emit_kv(path, str(key), val)

        if scalar_items:
            lines.append("")

        for key, val in nested_items.items():
            walk(val, path + (str(key),))

    # Root scalars (no table header)
    root_scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for key, val in root_scalars.items():
        emit_kv((), str(key), val)
    if root_scalars:
        lines.append("")

    # Tables
    for key, val in data.items():
        if isinstance(val, dict):
            walk(val, (str(key),))

    return "\n".join(lines).rstrip() + "\n"


def _generate_ini_template(role_prefix: str, parser: configparser.ConfigParser) -> str:
    """
    Generate an INI-style Jinja2 template from a ConfigParser.

    Quoting heuristic:
      foo = "bar" -> foo = "{{ prefix_section_foo }}"
      num = 42    -> num = {{ prefix_section_num }}
    """
    lines: list[str] = []

    for section in parser.sections():
        lines.append(f"[{section}]")
        for key, value in parser.items(section, raw=True):
            path = (section, key)
            var_name = make_var_name(role_prefix, path)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                lines.append(f'{key} = "{{{{ {var_name} }}}}"')
            else:
                lines.append(f"{key} = {{{{ {var_name} }}}}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate_template(fmt: str, parsed: Any, role_prefix: str) -> str:
    """
    Dispatch to the appropriate template generator.
    """
    if fmt == "toml":
        if not isinstance(parsed, dict):
            raise TypeError("TOML parser result must be a dict")
        return _generate_toml_template(role_prefix, parsed)
    if fmt == "ini":
        if not isinstance(parsed, configparser.ConfigParser):
            raise TypeError("INI parser result must be a ConfigParser")
        return _generate_ini_template(role_prefix, parsed)
    raise ValueError(f"Unsupported format: {fmt}")
