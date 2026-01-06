from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import BaseHandler


@dataclass
class SystemdLine:
    kind: str  # 'blank' | 'comment' | 'section' | 'kv' | 'raw'
    raw: str
    lineno: int
    section: str | None = None
    key: str | None = None
    value: str | None = None
    comment: str = ""
    before_eq: str = ""
    leading_ws_after_eq: str = ""
    occ_index: int | None = None


@dataclass
class SystemdUnit:
    lines: list[SystemdLine]


class SystemdUnitHandler(BaseHandler):
    """
    Handler for systemd unit files.

    unit files are INI-like, but keys may repeat (e.g. multiple ExecStart= lines).
    We preserve repeated keys by indexing them when flattening and templating.
    """

    fmt = "systemd"

    def parse(self, path: Path) -> SystemdUnit:
        text = path.read_text(encoding="utf-8")
        return self._parse_text(text)

    def _parse_text(self, text: str) -> SystemdUnit:
        lines = text.splitlines(keepends=True)
        out: list[SystemdLine] = []
        current_section: str | None = None
        # counts per section+key to assign occ_index
        occ: dict[tuple[str, str], int] = {}

        for lineno, raw_line in enumerate(lines, start=1):
            content = raw_line.rstrip("\n")
            stripped = content.strip()

            if not stripped:
                out.append(SystemdLine(kind="blank", raw=raw_line, lineno=lineno))
                continue

            if stripped.startswith(("#", ";")):
                out.append(SystemdLine(kind="comment", raw=raw_line, lineno=lineno))
                continue

            # section header
            if (
                stripped.startswith("[")
                and stripped.endswith("]")
                and len(stripped) >= 2
            ):
                sec = stripped[1:-1].strip()
                current_section = sec
                out.append(
                    SystemdLine(
                        kind="section", raw=raw_line, lineno=lineno, section=sec
                    )
                )
                continue

            if "=" not in content:
                out.append(SystemdLine(kind="raw", raw=raw_line, lineno=lineno))
                continue

            eq_index = content.find("=")
            before_eq = content[:eq_index]
            after_eq = content[eq_index + 1 :]

            key = before_eq.strip()
            if not key:
                out.append(SystemdLine(kind="raw", raw=raw_line, lineno=lineno))
                continue

            # whitespace after '='
            value_ws_len = len(after_eq) - len(after_eq.lstrip(" \t"))
            leading_ws = after_eq[:value_ws_len]
            value_and_comment = after_eq[value_ws_len:]

            value_part, comment = self._split_inline_comment(
                value_and_comment, {"#", ";"}
            )
            value = value_part.strip()

            sec = current_section or "DEFAULT"
            k = (sec, key)
            idx = occ.get(k, 0)
            occ[k] = idx + 1

            out.append(
                SystemdLine(
                    kind="kv",
                    raw=raw_line,
                    lineno=lineno,
                    section=sec,
                    key=key,
                    value=value,
                    comment=comment,
                    before_eq=before_eq,
                    leading_ws_after_eq=leading_ws,
                    occ_index=idx,
                )
            )

        return SystemdUnit(lines=out)

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        if not isinstance(parsed, SystemdUnit):
            raise TypeError("systemd parse result must be a SystemdUnit")

        # determine duplicates per (section,key)
        counts: dict[tuple[str, str], int] = {}
        for ln in parsed.lines:
            if ln.kind == "kv" and ln.section and ln.key:
                counts[(ln.section, ln.key)] = counts.get((ln.section, ln.key), 0) + 1

        items: list[tuple[tuple[str, ...], Any]] = []
        for ln in parsed.lines:
            if ln.kind != "kv" or not ln.section or not ln.key:
                continue
            path: tuple[str, ...] = (ln.section, ln.key)
            if counts.get((ln.section, ln.key), 0) > 1 and ln.occ_index is not None:
                path = path + (str(ln.occ_index),)
            items.append((path, ln.value or ""))
        return items

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if not isinstance(parsed, SystemdUnit):
            raise TypeError("systemd parse result must be a SystemdUnit")
        # We template using parsed lines so we preserve original formatting/comments.
        counts: dict[tuple[str, str], int] = {}
        for ln in parsed.lines:
            if ln.kind == "kv" and ln.section and ln.key:
                counts[(ln.section, ln.key)] = counts.get((ln.section, ln.key), 0) + 1

        out_lines: list[str] = []
        for ln in parsed.lines:
            if ln.kind != "kv" or not ln.section or not ln.key:
                out_lines.append(ln.raw)
                continue

            path: tuple[str, ...] = (ln.section, ln.key)
            if counts.get((ln.section, ln.key), 0) > 1 and ln.occ_index is not None:
                path = path + (str(ln.occ_index),)
            var = self.make_var_name(role_prefix, path)

            v = (ln.value or "").strip()
            quoted = len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}
            if quoted:
                repl = f'{ln.before_eq}={ln.leading_ws_after_eq}"{{{{ {var} }}}}"{ln.comment}'
            else:
                repl = f"{ln.before_eq}={ln.leading_ws_after_eq}{{{{ {var} }}}}{ln.comment}"

            newline = "\n" if ln.raw.endswith("\n") else ""
            out_lines.append(repl + newline)

        return "".join(out_lines)
