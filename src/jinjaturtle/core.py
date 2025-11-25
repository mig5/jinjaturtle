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


class QuotedString(str):
    """Marker type for strings that must be double-quoted in YAML output."""

    pass


class _TurtleDumper(yaml.SafeDumper):
    """Custom YAML dumper that always double-quotes QuotedString values."""

    pass


def _quoted_str_representer(dumper: yaml.SafeDumper, data: QuotedString):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_TurtleDumper.add_representer(QuotedString, _quoted_str_representer)


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


def _split_inline_comment(text: str, comment_chars: set[str]) -> tuple[str, str]:
    """
    Split 'value   # comment' into (value_part, comment_part), where
    comment_part starts at the first unquoted comment character.

    comment_chars is e.g. {'#'} for TOML, {'#', ';'} for INI.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch in comment_chars and not in_single and not in_double:
            return text[:i], text[i:]
    return text, ""


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


def _generate_ini_template_from_text(role_prefix: str, text: str) -> str:
    """
    Generate a Jinja2 template for an INI/php.ini-style file, preserving
    comments, blank lines, and section headers by patching values in-place.
    """
    lines = text.splitlines(keepends=True)
    current_section: str | None = None
    out_lines: list[str] = []

    for raw_line in lines:
        line = raw_line
        stripped = line.lstrip()

        # Blank or pure comment: keep as-is
        if not stripped or stripped[0] in {"#", ";"}:
            out_lines.append(raw_line)
            continue

        # Section header
        if stripped.startswith("[") and "]" in stripped:
            header_inner = stripped[1 : stripped.index("]")]
            current_section = header_inner.strip()
            out_lines.append(raw_line)
            continue

        # Work without newline so we can re-attach it exactly
        newline = ""
        content = raw_line
        if content.endswith("\r\n"):
            newline = "\r\n"
            content = content[:-2]
        elif content.endswith("\n"):
            newline = content[-1]
            content = content[:-1]

        eq_index = content.find("=")
        if eq_index == -1:
            # Not a simple key=value line: leave untouched
            out_lines.append(raw_line)
            continue

        before_eq = content[:eq_index]
        after_eq = content[eq_index + 1 :]

        key = before_eq.strip()
        if not key:
            out_lines.append(raw_line)
            continue

        # Whitespace after '='
        value_ws_len = len(after_eq) - len(after_eq.lstrip(" \t"))
        leading_ws = after_eq[:value_ws_len]
        value_and_comment = after_eq[value_ws_len:]

        value_part, comment_part = _split_inline_comment(value_and_comment, {"#", ";"})
        raw_value = value_part.strip()

        path = (key,) if current_section is None else (current_section, key)
        var_name = make_var_name(role_prefix, path)

        # Was the original value quoted?
        use_quotes = (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in {'"', "'"}
        )

        if use_quotes:
            quote_char = raw_value[0]
            replacement_value = f"{quote_char}{{{{ {var_name} }}}}{quote_char}"
        else:
            replacement_value = f"{{{{ {var_name} }}}}"

        new_content = before_eq + "=" + leading_ws + replacement_value + comment_part
        out_lines.append(new_content + newline)

    return "".join(out_lines)


def _generate_toml_template_from_text(role_prefix: str, text: str) -> str:
    """
    Generate a Jinja2 template for a TOML file, preserving comments,
    blank lines, and table headers by patching values in-place.

    Handles inline tables like:
      temp_targets = { cpu = 79.5, case = 72.0 }

    by mapping them to:
      temp_targets = { cpu = {{ prefix_database_temp_targets_cpu }},
                       case = {{ prefix_database_temp_targets_case }} }
    """
    lines = text.splitlines(keepends=True)
    current_table: tuple[str, ...] = ()
    out_lines: list[str] = []

    for raw_line in lines:
        line = raw_line
        stripped = line.lstrip()

        # Blank or pure comment
        if not stripped or stripped.startswith("#"):
            out_lines.append(raw_line)
            continue

        # Table header: [server] or [server.tls] or [[array.of.tables]]
        if stripped.startswith("[") and "]" in stripped:
            header = stripped
            first_bracket = header.find("[")
            closing_bracket = header.find("]", first_bracket + 1)
            if first_bracket != -1 and closing_bracket != -1:
                inner = header[first_bracket + 1 : closing_bracket].strip()
                inner = inner.strip("[]")  # handle [[table]] as well
                parts = [p.strip() for p in inner.split(".") if p.strip()]
                current_table = tuple(parts)
            out_lines.append(raw_line)
            continue

        # Try key = value
        newline = ""
        content = raw_line
        if content.endswith("\r\n"):
            newline = "\r\n"
            content = content[:-2]
        elif content.endswith("\n"):
            newline = content[-1]
            content = content[:-1]

        eq_index = content.find("=")
        if eq_index == -1:
            out_lines.append(raw_line)
            continue

        before_eq = content[:eq_index]
        after_eq = content[eq_index + 1 :]

        key = before_eq.strip()
        if not key:
            out_lines.append(raw_line)
            continue

        # Whitespace after '='
        value_ws_len = len(after_eq) - len(after_eq.lstrip(" \t"))
        leading_ws = after_eq[:value_ws_len]
        value_and_comment = after_eq[value_ws_len:]

        value_part, comment_part = _split_inline_comment(value_and_comment, {"#"})
        raw_value = value_part.strip()

        # Path for this key (table + key)
        path = current_table + (key,)

        # Special case: inline table
        if (
            raw_value.startswith("{")
            and raw_value.endswith("}")
            and tomllib is not None
        ):
            try:
                # Parse the inline table as a tiny TOML document
                mini_source = "table = " + raw_value + "\n"
                mini_data = tomllib.loads(mini_source)["table"]
            except Exception:
                mini_data = None

            if isinstance(mini_data, dict):
                inner_bits: list[str] = []
                for sub_key, sub_val in mini_data.items():
                    nested_path = path + (sub_key,)
                    nested_var = make_var_name(role_prefix, nested_path)
                    if isinstance(sub_val, str):
                        inner_bits.append(f'{sub_key} = "{{{{ {nested_var} }}}}"')
                    else:
                        inner_bits.append(f"{sub_key} = {{{{ {nested_var} }}}}")
                replacement_value = "{ " + ", ".join(inner_bits) + " }"
                new_content = (
                    before_eq + "=" + leading_ws + replacement_value + comment_part
                )
                out_lines.append(new_content + newline)
                continue
            # If parsing fails, fall through to normal handling

        # Normal scalar value handling (including bools, numbers, strings)
        var_name = make_var_name(role_prefix, path)
        use_quotes = (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in {'"', "'"}
        )

        if use_quotes:
            quote_char = raw_value[0]
            replacement_value = f"{quote_char}{{{{ {var_name} }}}}{quote_char}"
        else:
            replacement_value = f"{{{{ {var_name} }}}}"

        new_content = before_eq + "=" + leading_ws + replacement_value + comment_part
        out_lines.append(new_content + newline)

    return "".join(out_lines)


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
    the parsed structure (no comments).
    """
    if original_text is not None:
        if fmt == "toml":
            return _generate_toml_template_from_text(role_prefix, original_text)
        if fmt == "ini":
            return _generate_ini_template_from_text(role_prefix, original_text)
        raise ValueError(f"Unsupported format: {fmt}")

    # Fallback: previous behaviour (no comments preserved)
    if fmt == "toml":
        if not isinstance(parsed, dict):
            raise TypeError("TOML parser result must be a dict")
        return _generate_toml_template(role_prefix, parsed)
    if fmt == "ini":
        if not isinstance(parsed, configparser.ConfigParser):
            raise TypeError("INI parser result must be a ConfigParser")
        return _generate_ini_template(role_prefix, parsed)
    raise ValueError(f"Unsupported format: {fmt}")
