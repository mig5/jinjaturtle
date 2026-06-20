from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from . import BaseHandler
from .. import j2


_SECTION_KEYWORDS = {"host", "match"}


@dataclass
class SshConfigLine:
    kind: str  # 'blank' | 'comment' | 'kv' | 'raw'
    raw: str
    lineno: int
    key: str | None = None
    value: str | None = None
    processed_value: str | None = None
    comment: str = ""
    whitespace_before_comment: str = ""
    before_value: str = ""
    newline: str = ""
    quoted: bool = False
    is_section: bool = False
    section_kind: str | None = None
    section_label: str | None = None
    context_kind: str | None = None
    context_label: str | None = None
    occ_index: int | None = None


@dataclass
class SshConfig:
    lines: list[SshConfigLine]


class SshConfigHandler(BaseHandler):
    """
    Handler for OpenSSH ssh_config/sshd_config-style files.

    The format is intentionally treated as keyword + raw argument string rather
    than attempting to understand the argument type for every OpenSSH keyword.
    This preserves directives whose values are naturally whitespace-separated,
    comma-separated, colon-separated, commands, token strings, or quoted strings.

    Host and Match are treated as section headers for variable naming while
    still templating their criteria like ordinary keyword/argument lines.
    """

    fmt = "ssh"

    def parse(self, path: Path) -> SshConfig:
        text = path.read_text(encoding="utf-8")
        return self._parse_text(text)

    def _parse_text(self, text: str) -> SshConfig:
        lines = text.splitlines(keepends=True)
        out: list[SshConfigLine] = []
        current_kind: str | None = None
        current_label: str | None = None
        for lineno, raw_line in enumerate(lines, start=1):
            content, newline = self._split_newline(raw_line)
            stripped = content.strip()

            if not stripped:
                out.append(
                    SshConfigLine(
                        kind="blank", raw=raw_line, lineno=lineno, newline=newline
                    )
                )
                continue

            if content.lstrip(" \t").startswith("#"):
                out.append(
                    SshConfigLine(
                        kind="comment", raw=raw_line, lineno=lineno, newline=newline
                    )
                )
                continue

            parsed = self._split_keyword_value(content)
            if parsed is None:
                out.append(
                    SshConfigLine(
                        kind="raw", raw=raw_line, lineno=lineno, newline=newline
                    )
                )
                continue

            key, before_value, value_and_comment = parsed
            value_part, comment = self._split_inline_comment(value_and_comment, {"#"})
            whitespace_before_comment = value_part[len(value_part.rstrip(" \t")) :]
            raw_value = value_part.strip()

            quoted = (
                len(raw_value) >= 2
                and raw_value[0] == raw_value[-1]
                and raw_value[0] in {'"', "'"}
            )
            processed_value = raw_value[1:-1] if quoted else raw_value

            key_lower = key.lower()
            is_section = key_lower in _SECTION_KEYWORDS
            section_kind = key if is_section else None
            section_label = None
            context_kind = current_kind
            context_label = current_label

            if is_section:
                # Host/Match lines define the context for subsequent lines, but
                # the line itself is kept globally named as the section marker.
                base_label = self._section_label(processed_value, lineno)
                section_label = base_label
                current_kind = key
                current_label = base_label
                context_kind = None
                context_label = None

            out.append(
                SshConfigLine(
                    kind="kv",
                    raw=raw_line,
                    lineno=lineno,
                    key=key,
                    value=raw_value,
                    processed_value=processed_value,
                    comment=comment,
                    whitespace_before_comment=whitespace_before_comment,
                    before_value=before_value,
                    newline=newline,
                    quoted=quoted,
                    is_section=is_section,
                    section_kind=section_kind,
                    section_label=section_label,
                    context_kind=context_kind,
                    context_label=context_label,
                )
            )

        unit = SshConfig(lines=out)
        self._assign_occurrences(unit)
        return unit

    @staticmethod
    def _split_newline(raw_line: str) -> tuple[str, str]:
        if raw_line.endswith("\r\n"):
            return raw_line[:-2], "\r\n"
        if raw_line.endswith("\n"):
            return raw_line[:-1], "\n"
        return raw_line, ""

    @staticmethod
    def _split_keyword_value(content: str) -> tuple[str, str, str] | None:
        """Return (key, text_before_value, value_and_comment)."""
        m = re.match(r"^([ \t]*)([^\s=#]+)(.*)$", content)
        if not m:
            return None

        leading, key, rest = m.groups()
        # Keep the original spelling/indentation for rendering, but allow either:
        #   Keyword value
        #   Keyword=value
        #   Keyword = value
        i = 0
        while i < len(rest) and rest[i] in " \t":
            i += 1
        if i < len(rest) and rest[i] == "=":
            i += 1
            while i < len(rest) and rest[i] in " \t":
                i += 1
        elif i == 0 and rest:
            # Some unexpected non-whitespace separator. Treat as raw.
            return None

        before_value = leading + key + rest[:i]
        value_and_comment = rest[i:]
        return key, before_value, value_and_comment

    def _section_label(self, value: str | None, lineno: int) -> str:
        raw = (value or "").strip()
        if not raw:
            return f"line_{lineno}"
        # Use the same sanitisation semantics as variable names, but without a
        # role prefix. This keeps labels such as '*' useful instead of empty.
        label = self.make_var_name("x", (raw,))
        if label.startswith("x_"):
            label = label[2:]
        elif label == "x":
            label = "all"
        label = re.sub(r"_+", "_", label).strip("_")
        return label or f"line_{lineno}"

    def _base_path(self, ln: SshConfigLine) -> tuple[str, ...]:
        if ln.kind != "kv" or not ln.key:
            return ()
        if ln.is_section and ln.section_kind and ln.section_label:
            return (ln.section_kind, ln.section_label)
        if ln.context_kind and ln.context_label:
            return (ln.context_kind, ln.context_label, ln.key)
        return (ln.key,)

    def _assign_occurrences(self, parsed: SshConfig) -> None:
        counts: dict[tuple[str, ...], int] = {}
        for ln in parsed.lines:
            if ln.kind == "kv":
                base = self._base_path(ln)
                if base:
                    counts[base] = counts.get(base, 0) + 1

        seen: dict[tuple[str, ...], int] = {}
        for ln in parsed.lines:
            if ln.kind != "kv":
                continue
            base = self._base_path(ln)
            if not base:
                continue
            idx = seen.get(base, 0)
            seen[base] = idx + 1
            if counts.get(base, 0) > 1:
                ln.occ_index = idx

    def _path_for_line(self, ln: SshConfigLine) -> tuple[str, ...]:
        base = self._base_path(ln)
        if ln.occ_index is not None:
            return base + (str(ln.occ_index),)
        return base

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        if not isinstance(parsed, SshConfig):
            raise TypeError("SSH config parse result must be an SshConfig")

        items: list[tuple[tuple[str, ...], Any]] = []
        for ln in parsed.lines:
            if ln.kind != "kv":
                continue
            path = self._path_for_line(ln)
            if not path:
                continue
            items.append((path, ln.processed_value or ""))
        return items

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if not isinstance(parsed, SshConfig):
            raise TypeError("SSH config parse result must be an SshConfig")

        out_lines: list[str] = []
        for ln in parsed.lines:
            if ln.kind != "kv":
                out_lines.append(ln.raw)
                continue

            path = self._path_for_line(ln)
            if not path:
                out_lines.append(ln.raw)
                continue

            var = self.make_var_name(role_prefix, path)
            if ln.quoted and ln.value:
                quote_char = ln.value[0]
                replacement_value = j2.quoted_variable(var, quote_char)
            else:
                replacement_value = j2.variable(var)

            rendered = (
                f"{ln.before_value}{replacement_value}"
                f"{ln.whitespace_before_comment}{ln.comment}{ln.newline}"
            )
            out_lines.append(rendered)

        return "".join(out_lines)
