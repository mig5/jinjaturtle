from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

from . import BaseHandler


class IniHandler(BaseHandler):
    fmt = "ini"

    def parse(self, path: Path) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.optionxform = str  # noqa
        with path.open("r", encoding="utf-8") as f:
            parser.read_file(f)
        return parser

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        if not isinstance(parsed, configparser.ConfigParser):
            raise TypeError("INI parser result must be a ConfigParser")
        parser: configparser.ConfigParser = parsed
        items: list[tuple[tuple[str, ...], Any]] = []
        for section in parser.sections():
            for key, value in parser.items(section, raw=True):
                raw = value.strip()
                if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
                    processed: Any = raw[1:-1]
                else:
                    processed = raw
                items.append(((section, key), processed))
        return items

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if original_text is not None:
            return self._generate_ini_template_from_text(role_prefix, original_text)
        if not isinstance(parsed, configparser.ConfigParser):
            raise TypeError("INI parser result must be a ConfigParser")
        return self._generate_ini_template(role_prefix, parsed)

    def _generate_ini_template(
        self, role_prefix: str, parser: configparser.ConfigParser
    ) -> str:
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
                var_name = self.make_var_name(role_prefix, path)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    lines.append(f'{key} = "{{{{ {var_name} }}}}"')
                else:
                    lines.append(f"{key} = {{{{ {var_name} }}}}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _generate_ini_template_from_text(self, role_prefix: str, text: str) -> str:
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

            value_part, comment_part = self._split_inline_comment(
                value_and_comment, {"#", ";"}
            )
            raw_value = value_part.strip()

            path = (key,) if current_section is None else (current_section, key)
            var_name = self.make_var_name(role_prefix, path)

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

            new_content = (
                before_eq + "=" + leading_ws + replacement_value + comment_part
            )
            out_lines.append(new_content + newline)

        return "".join(out_lines)
