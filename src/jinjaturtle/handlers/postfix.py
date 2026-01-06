from __future__ import annotations

from pathlib import Path
from typing import Any

from . import BaseHandler


class PostfixMainHandler(BaseHandler):
    """
    Handler for Postfix main.cf style configuration.

    Postfix main.cf is largely 'key = value' with:
      - '#' comments
      - continuation lines starting with whitespace (they continue the previous value)
    """

    fmt = "postfix"

    def parse(self, path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8")
        return self._parse_text_to_dict(text)

    def _parse_text_to_dict(self, text: str) -> dict[str, str]:
        lines = text.splitlines()
        out: dict[str, str] = {}
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            if "=" not in line:
                i += 1
                continue

            eq_index = line.find("=")
            key = line[:eq_index].strip()
            if not key:
                i += 1
                continue

            # value + inline comment
            after = line[eq_index + 1 :]
            value_part, _comment = self._split_inline_comment(after, {"#"})
            value = value_part.strip()

            # collect continuation lines
            j = i + 1
            cont_parts: list[str] = []
            while j < len(lines):
                nxt = lines[j]
                if not nxt:
                    break
                if nxt.startswith((" ", "\t")):
                    if nxt.strip().startswith("#"):
                        # a commented continuation line - treat as a break
                        break
                    cont_parts.append(nxt.strip())
                    j += 1
                    continue
                break

            if cont_parts:
                value = " ".join([value] + cont_parts).strip()

            out[key] = value
            i = j if cont_parts else i + 1

        return out

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        if not isinstance(parsed, dict):
            raise TypeError("Postfix parse result must be a dict[str, str]")
        items: list[tuple[tuple[str, ...], Any]] = []
        for k, v in parsed.items():
            items.append(((k,), v))
        return items

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if original_text is None:
            # Canonical render (lossy)
            if not isinstance(parsed, dict):
                raise TypeError("Postfix parse result must be a dict[str, str]")
            lines: list[str] = []
            for k, v in parsed.items():
                var = self.make_var_name(role_prefix, (k,))
                lines.append(f"{k} = {{{{ {var} }}}}")
            return "\n".join(lines).rstrip() + "\n"
        return self._generate_from_text(role_prefix, original_text)

    def _generate_from_text(self, role_prefix: str, text: str) -> str:
        lines = text.splitlines(keepends=True)
        out_lines: list[str] = []
        i = 0
        while i < len(lines):
            raw_line = lines[i]
            content = raw_line.rstrip("\n")
            newline = "\n" if raw_line.endswith("\n") else ""

            stripped = content.strip()
            if not stripped:
                out_lines.append(raw_line)
                i += 1
                continue
            if stripped.startswith("#"):
                out_lines.append(raw_line)
                i += 1
                continue

            if "=" not in content:
                out_lines.append(raw_line)
                i += 1
                continue

            eq_index = content.find("=")
            before_eq = content[:eq_index]
            after_eq = content[eq_index + 1 :]

            key = before_eq.strip()
            if not key:
                out_lines.append(raw_line)
                i += 1
                continue

            # whitespace after '='
            value_ws_len = len(after_eq) - len(after_eq.lstrip(" \t"))
            leading_ws = after_eq[:value_ws_len]
            value_and_comment = after_eq[value_ws_len:]

            value_part, comment_part = self._split_inline_comment(
                value_and_comment, {"#"}
            )
            value = value_part.strip()

            # collect continuation physical lines to skip
            j = i + 1
            cont_parts: list[str] = []
            while j < len(lines):
                nxt_raw = lines[j]
                nxt = nxt_raw.rstrip("\n")
                if (
                    nxt.startswith((" ", "\t"))
                    and nxt.strip()
                    and not nxt.strip().startswith("#")
                ):
                    cont_parts.append(nxt.strip())
                    j += 1
                    continue
                break

            if cont_parts:
                value = " ".join([value] + cont_parts).strip()

            var = self.make_var_name(role_prefix, (key,))
            v = value
            quoted = len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}
            if quoted:
                replacement = (
                    f'{before_eq}={leading_ws}"{{{{ {var} }}}}"{comment_part}{newline}'
                )
            else:
                replacement = (
                    f"{before_eq}={leading_ws}{{{{ {var} }}}}{comment_part}{newline}"
                )

            out_lines.append(replacement)
            i = j  # skip continuation lines (if any)

        return "".join(out_lines)
