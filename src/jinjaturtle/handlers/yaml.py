from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from . import DictLikeHandler


class YamlHandler(DictLikeHandler):
    fmt = "yaml"
    flatten_lists = True  # you flatten YAML lists

    def parse(self, path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}

    def generate_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if original_text is not None:
            return self._generate_yaml_template_from_text(role_prefix, original_text)
        if not isinstance(parsed, (dict, list)):
            raise TypeError("YAML parser result must be a dict or list")
        dumped = yaml.safe_dump(parsed, sort_keys=False)
        return self._generate_yaml_template_from_text(role_prefix, dumped)

    def _generate_yaml_template_from_text(
        self,
        role_prefix: str,
        text: str,
    ) -> str:
        """
        Generate a Jinja2 template for a YAML file, preserving comments and
        blank lines by patching scalar values in-place.

        This handles common "config-ish" YAML:
          - top-level and nested mappings
          - lists of scalars
          - lists of small mapping objects
        It does *not* aim to support all YAML edge cases (anchors, tags, etc.).
        """
        lines = text.splitlines(keepends=True)
        out_lines: list[str] = []

        # Simple indentation-based context stack: (indent, path, kind)
        # kind is "map" or "seq".
        stack: list[tuple[int, tuple[str, ...], str]] = []

        # Track index per parent path for sequences
        seq_counters: dict[tuple[str, ...], int] = {}

        def current_path() -> tuple[str, ...]:
            return stack[-1][1] if stack else ()

        for raw_line in lines:
            stripped = raw_line.lstrip()
            indent = len(raw_line) - len(stripped)

            # Blank or pure comment lines unchanged
            if not stripped or stripped.startswith("#"):
                out_lines.append(raw_line)
                continue

            # Adjust stack based on indent
            while stack and indent < stack[-1][0]:
                stack.pop()

            # --- Handle mapping key lines: "key:" or "key: value"
            if ":" in stripped and not stripped.lstrip().startswith("- "):
                # separate key and rest
                key_part, rest = stripped.split(":", 1)
                key = key_part.strip()
                if not key:
                    out_lines.append(raw_line)
                    continue

                # Is this just "key:" or "key: value"?
                rest_stripped = rest.lstrip(" \t")

                # Use the same inline-comment splitter to see if there's any real value
                value_candidate, _ = self._split_inline_comment(rest_stripped, {"#"})
                has_value = bool(value_candidate.strip())

                # Update stack/context: current mapping at this indent
                # Replace any existing mapping at same indent
                if stack and stack[-1][0] == indent and stack[-1][2] == "map":
                    stack.pop()
                path = current_path() + (key,)
                stack.append((indent, path, "map"))

                if not has_value:
                    # Just "key:" -> collection or nested structure begins on following lines.
                    out_lines.append(raw_line)
                    continue

                # We have an inline scalar value on this same line.

                # Separate value from inline comment
                value_part, comment_part = self._split_inline_comment(
                    rest_stripped, {"#"}
                )
                raw_value = value_part.strip()
                var_name = self.make_var_name(role_prefix, path)

                # Keep quote-style if original was quoted
                use_quotes = (
                    len(raw_value) >= 2
                    and raw_value[0] == raw_value[-1]
                    and raw_value[0] in {'"', "'"}
                )

                if use_quotes:
                    q = raw_value[0]
                    replacement = f"{q}{{{{ {var_name} }}}}{q}"
                else:
                    replacement = f"{{{{ {var_name} }}}}"

                leading = rest[: len(rest) - len(rest.lstrip(" \t"))]
                new_stripped = f"{key}: {leading}{replacement}{comment_part}"
                out_lines.append(
                    " " * indent
                    + new_stripped
                    + ("\n" if raw_line.endswith("\n") else "")
                )
                continue

            # --- Handle list items: "- value" or "- key: value"
            if stripped.startswith("- "):
                # Determine parent path
                # If top of stack isn't sequence at this indent, push one using current path
                if not stack or stack[-1][0] != indent or stack[-1][2] != "seq":
                    parent_path = current_path()
                    stack.append((indent, parent_path, "seq"))

                parent_path = stack[-1][1]
                content = stripped[2:]  # after "- "
                parent_path = stack[-1][1]
                content = stripped[2:]  # after "- "

                # Determine index for this parent path
                index = seq_counters.get(parent_path, 0)
                seq_counters[parent_path] = index + 1

                path = parent_path + (str(index),)

                value_part, comment_part = self._split_inline_comment(content, {"#"})
                raw_value = value_part.strip()
                var_name = self.make_var_name(role_prefix, path)

                # If it's of the form "key: value" inside the list, we could try to
                # support that, but a simple scalar is the common case:
                use_quotes = (
                    len(raw_value) >= 2
                    and raw_value[0] == raw_value[-1]
                    and raw_value[0] in {'"', "'"}
                )

                if use_quotes:
                    q = raw_value[0]
                    replacement = f"{q}{{{{ {var_name} }}}}{q}"
                else:
                    replacement = f"{{{{ {var_name} }}}}"

                new_stripped = f"- {replacement}{comment_part}"
                out_lines.append(
                    " " * indent
                    + new_stripped
                    + ("\n" if raw_line.endswith("\n") else "")
                )
                continue

            # Anything else (multi-line scalars, weird YAML): leave untouched
            out_lines.append(raw_line)

        return "".join(out_lines)
