from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from .dict import DictLikeHandler
from .. import j2
from ..escape import escape_jinja_literal
from ..loop_analyzer import is_safe_loop_field_key
from ..loop_analyzer import LoopCandidate


def _reject_recursive_structure(obj: Any) -> None:
    """Raise ``yaml.YAMLError`` if *obj* contains a reference cycle.

    A recursive YAML anchor (``a: &a [*a]``) produces a container that contains
    itself. Every consumer in JinjaTurtle walks the parsed object depth-first,
    so a cycle would raise ``RecursionError`` deep in unrelated code. Detect it
    up front by tracking the ``id()`` of containers on the current descent path;
    a repeat means a cycle. ``yaml.YAMLError`` is raised so ``parse_config``
    normalises it into a clean ``ConfigParseError`` like any other malformed
    input, rather than surfacing a stack-overflow traceback.
    """

    on_path: set[int] = set()

    def walk(node: Any) -> None:
        if isinstance(node, (dict, list)):
            marker = id(node)
            if marker in on_path:
                raise yaml.YAMLError(
                    "recursive/self-referential YAML structure is not supported"
                )
            on_path.add(marker)
            children = node.values() if isinstance(node, dict) else node
            for child in children:
                walk(child)
            on_path.discard(marker)

    walk(obj)


class YamlHandler(DictLikeHandler):
    """
    YAML handler that can generate both scalar templates and loop-based templates.
    """

    fmt = "yaml"
    flatten_lists = True

    def parse(self, path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text) or {}
        # PyYAML's safe_load happily builds *recursive* structures from an anchor
        # that references itself (e.g. ``a: &a [*a]``). Downstream flattening,
        # timestamp-stringifying and template generation all walk the parsed
        # object recursively and would blow the Python stack (RecursionError) on
        # such input. JinjaTurtle is regularly pointed at harvested,
        # attacker-influenceable config, so reject a self-referential document
        # cleanly here rather than crashing later.
        _reject_recursive_structure(parsed)
        return parsed

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

    def _yaml_scalar_expr(self, var_name: str, raw_value: str | None = None) -> str:
        """Return a Jinja expression that preserves YAML scalar spelling.

        Plain ``{{ var }}`` renders Python booleans as ``True``/``False``.
        YAML config files conventionally use ``true``/``false`` and some
        consumers are stricter than PyYAML, so emit explicit YAML spelling for
        values that were originally YAML booleans/nulls.
        """
        return j2.yaml_scalar_expression(var_name, raw_value)

    def _yaml_value_expr(self, value_expr: str, sample_value: Any | None = None) -> str:
        return j2.yaml_value_expression(value_expr, sample_value)

    def _surrounding_quote(self, raw_value: str) -> str | None:
        if (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in {'"', "'"}
        ):
            return raw_value[0]
        return None

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
                out_lines.append(escape_jinja_literal(raw_line))
                continue

            while stack and indent < stack[-1][0]:
                stack.pop()

            if ":" in stripped and not stripped.lstrip().startswith("- "):
                key_part, rest = stripped.split(":", 1)
                key = key_part.strip()
                if not key:
                    out_lines.append(escape_jinja_literal(raw_line))
                    continue

                rest_stripped = rest.lstrip(" \t")
                value_candidate, _ = self._split_inline_comment(rest_stripped, {"#"})
                has_value = bool(value_candidate.strip())

                if stack and stack[-1][0] == indent and stack[-1][2] == "map":
                    stack.pop()
                path = current_path() + (key,)
                stack.append((indent, path, "map"))

                if not has_value:
                    out_lines.append(escape_jinja_literal(raw_line))
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
                    replacement = j2.quoted_variable(var_name, q)
                else:
                    replacement = self._yaml_scalar_expr(var_name, raw_value)

                leading = rest[: len(rest) - len(rest.lstrip(" \t"))]
                new_rest = f"{leading}{replacement}{escape_jinja_literal(comment_part)}"
                new_stripped = f"{escape_jinja_literal(key)}:{new_rest}"
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
                    replacement = j2.quoted_variable(var_name, q)
                else:
                    replacement = self._yaml_scalar_expr(var_name, raw_value)

                new_stripped = f"- {replacement}{escape_jinja_literal(comment_part)}"
                out_lines.append(
                    " " * indent
                    + new_stripped
                    + ("\n" if raw_line.endswith("\n") else "")
                )
                continue

            out_lines.append(escape_jinja_literal(raw_line))

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

        def first_sequence_item_style(
            start_index: int, parent_indent: int
        ) -> tuple[str | None, int | None]:
            for future_line in lines[start_index + 1 :]:
                future_stripped = future_line.lstrip()
                future_indent = len(future_line) - len(future_stripped)
                if not future_stripped or future_stripped.startswith("#"):
                    continue
                if future_indent < parent_indent:
                    return None, None
                if future_stripped.startswith("- "):
                    value_part, _comment_part = self._split_inline_comment(
                        future_stripped[2:], {"#"}
                    )
                    return self._surrounding_quote(value_part.strip()), future_indent
                if future_indent <= parent_indent:
                    return None, None
            return None, None

        def next_significant_line(index: int) -> tuple[int, str] | None:
            for future_line in lines[index + 1 :]:
                future_stripped = future_line.lstrip()
                if not future_stripped.strip() or future_stripped.startswith("#"):
                    continue
                return len(future_line) - len(future_stripped), future_stripped
            return None

        for line_index, raw_line in enumerate(lines):
            stripped = raw_line.lstrip()
            is_blank = not stripped.strip()
            is_comment = stripped.startswith("#")
            indent = len(raw_line) - len(stripped)

            # If we're skipping lines inside a collection replaced by a loop,
            # continue through YAML's indentless sequence style too, where list
            # items can appear at the same indentation as the parent key:
            #
            #   images:
            #   - ubuntu
            #   - debian
            #
            # Stop only when a non-list item at the parent indentation appears,
            # or when indentation moves above the parent collection.
            if skip_until_indent is not None:
                if is_blank:
                    next_line = next_significant_line(line_index)
                    if next_line is None:
                        skip_until_indent = None
                        out_lines.append(escape_jinja_literal(raw_line))
                    else:
                        next_indent, next_stripped = next_line
                        still_in_collection = next_indent > skip_until_indent or (
                            next_indent == skip_until_indent
                            and next_stripped.startswith("- ")
                        )
                        if not still_in_collection:
                            skip_until_indent = None
                            out_lines.append(escape_jinja_literal(raw_line))
                    continue
                if is_comment:
                    if indent <= skip_until_indent:
                        skip_until_indent = None
                        out_lines.append(escape_jinja_literal(raw_line))
                    # Comments/blank lines indented beneath the replaced
                    # collection are considered part of that collection and
                    # cannot be placed safely inside a generated loop.
                    continue
                if indent < skip_until_indent or (
                    indent == skip_until_indent and not stripped.startswith("- ")
                ):
                    skip_until_indent = None
                else:
                    continue  # Skip this line

            # Blank or comment lines
            if is_blank or is_comment:
                out_lines.append(escape_jinja_literal(raw_line))
                continue

            # Adjust stack based on indent
            while stack and indent < stack[-1][0]:
                stack.pop()

            # --- Handle mapping key lines: "key:" or "key: value"
            if ":" in stripped and not stripped.lstrip().startswith("- "):
                key_part, rest = stripped.split(":", 1)
                key = key_part.strip()
                if not key:
                    out_lines.append(escape_jinja_literal(raw_line))
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

                    scalar_quote, item_indent = first_sequence_item_style(
                        line_index, indent
                    )
                    if candidate.item_schema != "scalar":
                        scalar_quote = None

                    # Generate loop
                    loop_str = self._generate_yaml_loop(
                        candidate,
                        role_prefix,
                        indent,
                        scalar_quote=scalar_quote,
                        item_indent=item_indent,
                    )
                    out_lines.append(loop_str)

                    # Skip subsequent lines that are part of this collection
                    skip_until_indent = indent
                    continue

                if not has_value:
                    out_lines.append(escape_jinja_literal(raw_line))
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
                    replacement = j2.quoted_variable(var_name, q)
                else:
                    replacement = self._yaml_scalar_expr(var_name, raw_value)

                leading = rest[: len(rest) - len(rest.lstrip(" \t"))]
                new_rest = f"{leading}{replacement}{escape_jinja_literal(comment_part)}"
                new_stripped = f"{escape_jinja_literal(key)}:{new_rest}"
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
                content = stripped[2:]
                value_part, _comment_part = self._split_inline_comment(content, {"#"})
                raw_value = value_part.strip()
                scalar_quote = self._surrounding_quote(raw_value)

                # Check if parent path is a loop candidate
                if parent_path in loop_paths:
                    # Find the matching candidate
                    candidate = next(
                        c for c in loop_candidates if c.path == parent_path
                    )

                    # Generate loop (with indent for the '-' items)
                    loop_str = self._generate_yaml_loop(
                        candidate,
                        role_prefix,
                        indent,
                        is_list=True,
                        scalar_quote=scalar_quote,
                    )
                    out_lines.append(loop_str)

                    # Skip subsequent items
                    skip_until_indent = indent - 1 if indent > 0 else None
                    continue

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
                    replacement = j2.quoted_variable(var_name, q)
                else:
                    replacement = self._yaml_scalar_expr(var_name, raw_value)

                new_stripped = f"- {replacement}{escape_jinja_literal(comment_part)}"
                out_lines.append(
                    " " * indent
                    + new_stripped
                    + ("\n" if raw_line.endswith("\n") else "")
                )
                continue

            out_lines.append(escape_jinja_literal(raw_line))

        return "".join(out_lines)

    def _generate_yaml_loop(
        self,
        candidate: LoopCandidate,
        role_prefix: str,
        indent: int,
        is_list: bool = False,
        scalar_quote: str | None = None,
        item_indent: int | None = None,
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

        lines: list[str] = []
        if not is_list:
            key = candidate.path[-1] if candidate.path else "items"
            lines.append(f"{indent_str}{escape_jinja_literal(str(key))}:")

        item_lines: list[str] = []
        if candidate.items:
            sample_item = candidate.items[0]
            effective_item_indent = (
                item_indent if item_indent is not None else indent + 2
            )
            if is_list:
                effective_item_indent = indent
            item_indent_str = " " * effective_item_indent

            if candidate.item_schema == "scalar":
                value_expr = self._yaml_value_expr(item_var, sample_item)
                if scalar_quote and isinstance(sample_item, str):
                    value_expr = j2.quoted_variable(item_var, scalar_quote)
                item_lines.append(f"{item_indent_str}- {value_expr}")
            elif candidate.item_schema in ("simple_dict", "nested"):
                item_lines = self._dict_to_yaml_lines(
                    sample_item, item_var, item_indent, is_list_item=True
                )

        if item_lines:
            # Put the first YAML item on the same physical line as the Jinja
            # ``for`` tag. With default Jinja whitespace settings this avoids
            # rendering a blank line after the parent key. Keeping the control
            # tag itself at column zero prevents its indentation from leaking
            # into the rendered YAML and nesting the next top-level key.
            lines.append(f"{j2.for_start(item_var, collection_var)}{item_lines[0]}")
            lines.extend(item_lines[1:])
            lines.append(j2.for_end(keep_trailing_newline=True))
        else:
            lines.append(j2.for_start(item_var, collection_var))
            lines.append(j2.for_end(keep_trailing_newline=True))

        return "\n".join(lines)

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

            # Defence in depth: the loop analyzer already refuses a dict-loop
            # whose items contain a non-identifier key (see _analyze_dict_schema),
            # so ``key`` should always be a plain identifier here. Assert it
            # rather than interpolate a raw key into a Jinja reference: a key such
            # as ``a }}{{ x`` would otherwise close the placeholder and inject a
            # live construct that the output gate cannot distinguish from a
            # legitimate variable.
            if not is_safe_loop_field_key(key):
                raise ValueError(
                    f"refusing to emit loop-item field reference for unsafe key: {key!r}"
                )

            if first_key and is_list_item:
                # First key gets the list marker
                value_expr = self._yaml_value_expr(f"{loop_var}.{key}", value)
                lines.append(
                    f"{indent_str}- {escape_jinja_literal(str(key))}: {value_expr}"
                )
                first_key = False
            else:
                # Subsequent keys are indented
                sub_indent = indent + 2 if is_list_item else indent
                value_expr = self._yaml_value_expr(f"{loop_var}.{key}", value)
                lines.append(
                    f"{' ' * sub_indent}{escape_jinja_literal(str(key))}: {value_expr}"
                )

        return lines
