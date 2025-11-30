from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from .dict import DictLikeHandler
from ..loop_analyzer import LoopCandidate


class YamlHandler(DictLikeHandler):
    """
    YAML handler that can generate both scalar templates and loop-based templates.
    """

    fmt = "yaml"
    flatten_lists = True

    def parse(self, path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        """Original scalar-only template generation."""
        if original_text is not None:
            return self._generate_yaml_template_from_text(role_prefix, original_text)
        if not isinstance(parsed, (dict, list)):
            raise TypeError("YAML parser result must be a dict or list")
        dumped = yaml.safe_dump(parsed, sort_keys=False)
        return self._generate_yaml_template_from_text(role_prefix, dumped)

    def generate_jinja2_template_with_loops(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None,
        loop_candidates: list[LoopCandidate],
    ) -> str:
        """Generate template with Jinja2 for loops where appropriate."""

        # Build loop path set for quick lookup
        loop_paths = {candidate.path for candidate in loop_candidates}

        if original_text is not None:
            return self._generate_yaml_template_with_loops_from_text(
                role_prefix, original_text, loop_candidates, loop_paths
            )

        if not isinstance(parsed, (dict, list)):
            raise TypeError("YAML parser result must be a dict or list")

        dumped = yaml.safe_dump(parsed, sort_keys=False)
        return self._generate_yaml_template_with_loops_from_text(
            role_prefix, dumped, loop_candidates, loop_paths
        )

    def _generate_yaml_template_from_text(
        self,
        role_prefix: str,
        text: str,
    ) -> str:
        """Original scalar-only template generation (unchanged from base)."""
        lines = text.splitlines(keepends=True)
        out_lines: list[str] = []

        stack: list[tuple[int, tuple[str, ...], str]] = []
        seq_counters: dict[tuple[str, ...], int] = {}

        def current_path() -> tuple[str, ...]:
            return stack[-1][1] if stack else ()

        for raw_line in lines:
            stripped = raw_line.lstrip()
            indent = len(raw_line) - len(stripped)

            if not stripped or stripped.startswith("#"):
                out_lines.append(raw_line)
                continue

            while stack and indent < stack[-1][0]:
                stack.pop()

            if ":" in stripped and not stripped.lstrip().startswith("- "):
                key_part, rest = stripped.split(":", 1)
                key = key_part.strip()
                if not key:
                    out_lines.append(raw_line)
                    continue

                rest_stripped = rest.lstrip(" \t")
                value_candidate, _ = self._split_inline_comment(rest_stripped, {"#"})
                has_value = bool(value_candidate.strip())

                if stack and stack[-1][0] == indent and stack[-1][2] == "map":
                    stack.pop()
                path = current_path() + (key,)
                stack.append((indent, path, "map"))

                if not has_value:
                    out_lines.append(raw_line)
                    continue

                value_part, comment_part = self._split_inline_comment(
                    rest_stripped, {"#"}
                )
                raw_value = value_part.strip()
                var_name = self.make_var_name(role_prefix, path)

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
                new_rest = f"{leading}{replacement}{comment_part}"
                new_stripped = f"{key}:{new_rest}"
                out_lines.append(
                    " " * indent
                    + new_stripped
                    + ("\n" if raw_line.endswith("\n") else "")
                )
                continue

            if stripped.startswith("- "):
                if not stack or stack[-1][0] != indent or stack[-1][2] != "seq":
                    parent_path = current_path()
                    stack.append((indent, parent_path, "seq"))

                parent_path = stack[-1][1]
                content = stripped[2:]

                index = seq_counters.get(parent_path, 0)
                seq_counters[parent_path] = index + 1

                path = parent_path + (str(index),)

                value_part, comment_part = self._split_inline_comment(content, {"#"})
                raw_value = value_part.strip()
                var_name = self.make_var_name(role_prefix, path)

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

            out_lines.append(raw_line)

        return "".join(out_lines)

    def _generate_yaml_template_with_loops_from_text(
        self,
        role_prefix: str,
        text: str,
        loop_candidates: list[LoopCandidate],
        loop_paths: set[tuple[str, ...]],
    ) -> str:
        """
        Generate YAML template with Jinja2 for loops.

        Strategy:
        1. Parse YAML line-by-line maintaining context
        2. When we encounter a path that's a loop candidate:
           - Replace that section with a {% for %} loop
           - Use the first item as template structure
        3. Everything else gets scalar variable replacement
        """

        lines = text.splitlines(keepends=True)
        out_lines: list[str] = []

        stack: list[tuple[int, tuple[str, ...], str]] = []
        seq_counters: dict[tuple[str, ...], int] = {}

        # Track which lines are part of loop sections (to skip them)
        skip_until_indent: int | None = None

        def current_path() -> tuple[str, ...]:
            return stack[-1][1] if stack else ()

        for raw_line in lines:
            stripped = raw_line.lstrip()
            indent = len(raw_line) - len(stripped)

            # If we're skipping lines (inside a loop section), check if we can stop
            if skip_until_indent is not None:
                if (
                    indent <= skip_until_indent
                    and stripped
                    and not stripped.startswith("#")
                ):
                    skip_until_indent = None
                else:
                    continue  # Skip this line

            # Blank or comment lines
            if not stripped or stripped.startswith("#"):
                out_lines.append(raw_line)
                continue

            # Adjust stack based on indent
            while stack and indent < stack[-1][0]:
                stack.pop()

            # --- Handle mapping key lines: "key:" or "key: value"
            if ":" in stripped and not stripped.lstrip().startswith("- "):
                key_part, rest = stripped.split(":", 1)
                key = key_part.strip()
                if not key:
                    out_lines.append(raw_line)
                    continue

                rest_stripped = rest.lstrip(" \t")
                value_candidate, _ = self._split_inline_comment(rest_stripped, {"#"})
                has_value = bool(value_candidate.strip())

                if stack and stack[-1][0] == indent and stack[-1][2] == "map":
                    stack.pop()
                path = current_path() + (key,)
                stack.append((indent, path, "map"))

                # Check if this path is a loop candidate
                if path in loop_paths:
                    # Find the matching candidate
                    candidate = next(c for c in loop_candidates if c.path == path)

                    # Generate loop
                    loop_str = self._generate_yaml_loop(candidate, role_prefix, indent)
                    out_lines.append(loop_str)

                    # Skip subsequent lines that are part of this collection
                    skip_until_indent = indent
                    continue

                if not has_value:
                    out_lines.append(raw_line)
                    continue

                # Scalar value - replace with variable
                value_part, comment_part = self._split_inline_comment(
                    rest_stripped, {"#"}
                )
                raw_value = value_part.strip()
                var_name = self.make_var_name(role_prefix, path)

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
                new_rest = f"{leading}{replacement}{comment_part}"
                new_stripped = f"{key}:{new_rest}"
                out_lines.append(
                    " " * indent
                    + new_stripped
                    + ("\n" if raw_line.endswith("\n") else "")
                )
                continue

            # --- Handle list items: "- value" or "- key: value"
            if stripped.startswith("- "):
                if not stack or stack[-1][0] != indent or stack[-1][2] != "seq":
                    parent_path = current_path()
                    stack.append((indent, parent_path, "seq"))

                parent_path = stack[-1][1]

                # Check if parent path is a loop candidate
                if parent_path in loop_paths:
                    # Find the matching candidate
                    candidate = next(
                        c for c in loop_candidates if c.path == parent_path
                    )

                    # Generate loop (with indent for the '-' items)
                    loop_str = self._generate_yaml_loop(
                        candidate, role_prefix, indent, is_list=True
                    )
                    out_lines.append(loop_str)

                    # Skip subsequent items
                    skip_until_indent = indent - 1 if indent > 0 else None
                    continue

                content = stripped[2:]
                index = seq_counters.get(parent_path, 0)
                seq_counters[parent_path] = index + 1

                path = parent_path + (str(index),)

                value_part, comment_part = self._split_inline_comment(content, {"#"})
                raw_value = value_part.strip()
                var_name = self.make_var_name(role_prefix, path)

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

            out_lines.append(raw_line)

        return "".join(out_lines)

    def _generate_yaml_loop(
        self,
        candidate: LoopCandidate,
        role_prefix: str,
        indent: int,
        is_list: bool = False,
    ) -> str:
        """
        Generate a Jinja2 for loop for a YAML collection.

        Args:
            candidate: Loop candidate with items and metadata
            role_prefix: Variable prefix
            indent: Indentation level in spaces
            is_list: True if this is a YAML list, False if dict

        Returns:
            YAML string with Jinja2 loop
        """

        indent_str = " " * indent
        collection_var = self.make_var_name(role_prefix, candidate.path)
        item_var = candidate.loop_var

        lines = []

        if not is_list:
            # Dict-style: key: {% for ... %}
            key = candidate.path[-1] if candidate.path else "items"
            lines.append(f"{indent_str}{key}:")
            lines.append(f"{indent_str}  {{% for {item_var} in {collection_var} -%}}")
        else:
            # List-style: just the loop
            lines.append(f"{indent_str}{{% for {item_var} in {collection_var} -%}}")

        # Generate template for item structure
        if candidate.items:
            sample_item = candidate.items[0]
            item_indent = indent + 2 if not is_list else indent

            if candidate.item_schema == "scalar":
                # Simple list of scalars
                if is_list:
                    lines.append(f"{indent_str}- {{{{ {item_var} }}}}")
                else:
                    lines.append(f"{indent_str}  - {{{{ {item_var} }}}}")

            elif candidate.item_schema in ("simple_dict", "nested"):
                # List of dicts or complex items - these are ALWAYS list items in YAML
                item_lines = self._dict_to_yaml_lines(
                    sample_item, item_var, item_indent, is_list_item=True
                )
                lines.extend(item_lines)

        # Close loop
        close_indent = indent + 2 if not is_list else indent
        lines.append(f"{' ' * close_indent}{{% endfor %}}")

        return "\n".join(lines) + "\n"

    def _dict_to_yaml_lines(
        self,
        data: dict[str, Any],
        loop_var: str,
        indent: int,
        is_list_item: bool = False,
    ) -> list[str]:
        """
        Convert a dict to YAML lines with Jinja2 variable references.

        Args:
            data: Dict representing item structure
            loop_var: Loop variable name
            indent: Base indentation level
            is_list_item: True if this should start with '-'

        Returns:
            List of YAML lines
        """

        lines = []
        indent_str = " " * indent

        first_key = True
        for key, value in data.items():
            if key == "_key":
                # Special key for dict collections - output as comment or skip
                continue

            if first_key and is_list_item:
                # First key gets the list marker
                lines.append(f"{indent_str}- {key}: {{{{ {loop_var}.{key} }}}}")
                first_key = False
            else:
                # Subsequent keys are indented
                sub_indent = indent + 2 if is_list_item else indent
                lines.append(f"{' ' * sub_indent}{key}: {{{{ {loop_var}.{key} }}}}")

        return lines
