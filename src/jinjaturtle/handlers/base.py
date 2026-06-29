from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable


class BaseHandler:
    """
    Base class for a config format handler.

    Each handler is responsible for:
      - parse(path) -> parsed object
      - flatten(parsed) -> list[(path_tuple, value)]
      - generate_jinja2_template(parsed, role_prefix, original_text=None) -> str
    """

    fmt: str  # e.g. "ini", "yaml", ...

    def parse(self, path: Path) -> Any:
        raise NotImplementedError

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        raise NotImplementedError

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        raise NotImplementedError

    def _split_inline_comment(
        self, text: str, comment_chars: set[str]
    ) -> tuple[str, str]:
        """
        Split 'value # comment' into (value_part, comment_part), where
        comment_part starts at the first unquoted comment character.

        comment_chars is e.g. {'#'} for TOML/YAML, {'#', ';'} for INI.
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

    @staticmethod
    def make_var_name(role_prefix: str, path: Iterable[str]) -> str:
        """
        Build an Ansible var name like:
          role_prefix_section_subsection_key

        Sanitises parts to lowercase [a-z0-9_] and strips extras.

        Consecutive separators are collapsed to a single underscore. This is
        required for correctness, not just aesthetics: a source key such as
        ``log..level`` or ``cache--size`` would otherwise sanitise to a name
        containing a double underscore (``log__level``). The output safety gate
        in ``safety.py`` deliberately rejects *any* ``__`` in a generated
        identifier because ``__`` is the gateway to every Jinja2 SSTI gadget
        (``__class__``/``__globals__``/...). Emitting a dunder here would make
        JinjaTurtle's own gate reject JinjaTurtle's own placeholder, aborting
        generation on entirely benign config. Collapsing runs keeps every
        generated name a plain single-underscore-delimited identifier that the
        gate accepts.
        """
        role_prefix = re.sub(r"_+", "_", role_prefix.strip().lower())
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
            # Collapse runs of underscores (from adjacent separators) to a
            # single "_" so the result can never contain a forbidden "__".
            cleaned_part = re.sub(r"_+", "_", "".join(cleaned_chars)).strip("_")
            if cleaned_part:
                clean_parts.append(cleaned_part)

        if clean_parts:
            return role_prefix + "_" + "_".join(clean_parts)
        return role_prefix
