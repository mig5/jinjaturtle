from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from . import DictLikeHandler


class TomlHandler(DictLikeHandler):
    fmt = "toml"
    flatten_lists = False  # keep lists as scalars

    def parse(self, path: Path) -> Any:
        if tomllib is None:
            raise RuntimeError(
                "tomllib/tomli is required to parse TOML files but is not installed"
            )
        with path.open("rb") as f:
            return tomllib.load(f)

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if original_text is not None:
            return self._generate_toml_template_from_text(role_prefix, original_text)
        if not isinstance(parsed, dict):
            raise TypeError("TOML parser result must be a dict")
        return self._generate_toml_template(role_prefix, parsed)

    def _generate_toml_template(self, role_prefix: str, data: dict[str, Any]) -> str:
        """
        Generate a TOML Jinja2 template from parsed TOML dict.

        Values become Jinja placeholders, with quoting preserved for strings:
          foo = "bar" -> foo = "{{ prefix_foo }}"
          port = 8080 -> port = {{ prefix_port }}
        """
        lines: list[str] = []

        def emit_kv(path: tuple[str, ...], key: str, value: Any) -> None:
            var_name = self.make_var_name(role_prefix, path + (key,))
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

    def _generate_toml_template_from_text(self, role_prefix: str, text: str) -> str:
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

            value_part, comment_part = self._split_inline_comment(
                value_and_comment, {"#"}
            )
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
                        nested_var = self.make_var_name(role_prefix, nested_path)
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
            var_name = self.make_var_name(role_prefix, path)
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

            new_content = (
                before_eq + "=" + leading_ws + replacement_value + comment_part
            )
            out_lines.append(new_content + newline)

        return "".join(out_lines)
