from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import DictLikeHandler
from ..loop_analyzer import LoopCandidate


class JsonHandler(DictLikeHandler):
    fmt = "json"
    flatten_lists = True

    def parse(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def generate_jinja2_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        """Generate a scalar JSON template while preserving source formatting."""
        if not isinstance(parsed, (dict, list)):
            raise TypeError("JSON parser result must be a dict or list")
        if original_text is not None:
            return self._generate_json_template_from_text(role_prefix, original_text)
        return self._generate_json_template(role_prefix, parsed)

    JSON_INDENT = 2
    JSON_VALUE_FILTER = "to_json(ensure_ascii=False)"

    def _leading_indent(self, s: str, idx: int) -> int:
        """Return the number of leading spaces on the line containing idx."""
        line_start = s.rfind("\n", 0, idx) + 1
        indent = 0
        while line_start + indent < len(s) and s[line_start + indent] == " ":
            indent += 1
        return indent

    def _replace_marker_with_pretty_loop(
        self,
        s: str,
        marker: str,
        replacement_builder,
    ) -> str:
        """
        Replace a quoted marker with an indentation-aware multiline snippet.
        `marker` must include the surrounding JSON quotes, e.g. '"__LOOP_SCALAR__...__"'.
        """
        marker_re = re.compile(re.escape(marker))

        def _repl(m: re.Match[str]) -> str:
            base_indent = self._leading_indent(m.string, m.start())
            return replacement_builder(base_indent)

        return marker_re.sub(_repl, s)

    def generate_jinja2_template_with_loops(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None,
        loop_candidates: list[LoopCandidate],
    ) -> str:
        """Generate template with Jinja2 for loops where appropriate."""
        if not isinstance(parsed, (dict, list)):
            raise TypeError("JSON parser result must be a dict or list")

        # Build loop path set for quick lookup
        loop_paths = {candidate.path for candidate in loop_candidates}

        return self._generate_json_template_with_loops(
            role_prefix, parsed, loop_paths, loop_candidates
        )

    def _json_value_expr(self, var_name: str) -> str:
        """Return a Jinja expression for a JSON value.

        Jinja's built-in ``tojson`` filter is HTML-safe and therefore escapes
        characters such as ``<`` and ``>`` as ``\u003c``/``\u003e``. That is
        useful in HTML, but noisy in configuration files. JinjaTurtle generates
        Ansible templates, so use Ansible's ``to_json`` filter instead.
        """
        return f"{{{{ {var_name} | {self.JSON_VALUE_FILTER} }}}}"

    def _generate_json_template_from_text(self, role_prefix: str, text: str) -> str:
        """Replace JSON scalar values in-place, preserving original formatting.

        The older JSON path parsed the file and wrote it back with
        ``json.dumps(indent=2)``, which caused cosmetic diffs such as changing
        four-space indentation to two-space indentation and adding a final
        newline to files that intentionally lacked one. This scanner walks the
        original JSON source and only replaces scalar value tokens with Jinja2
        expressions; all whitespace, object/list indentation, key ordering, and
        final newline state are left untouched.
        """
        spans = self._collect_json_scalar_spans(text)
        if spans is None:
            # Should be rare because the caller has already parsed the JSON, but
            # keep the structural fallback rather than failing template creation.
            parsed = json.loads(text)
            return self._generate_json_template(role_prefix, parsed)

        chunks: list[str] = []
        pos = 0
        for path, start, end in spans:
            chunks.append(text[pos:start])
            chunks.append(self._json_value_expr(self.make_var_name(role_prefix, path)))
            pos = end
        chunks.append(text[pos:])
        return "".join(chunks)

    def _collect_json_scalar_spans(
        self, text: str
    ) -> list[tuple[tuple[str, ...], int, int]] | None:
        """Return source spans for JSON scalar *values*.

        Keys are parsed to determine the current path but are not returned.
        """
        decoder = json.JSONDecoder()
        spans: list[tuple[tuple[str, ...], int, int]] = []

        def skip_ws(i: int) -> int:
            while i < len(text) and text[i] in " \t\r\n":
                i += 1
            return i

        def raw_decode_at(i: int) -> tuple[Any, int]:
            return decoder.raw_decode(text, i)

        def parse_value(i: int, path: tuple[str, ...]) -> int:
            i = skip_ws(i)
            if i >= len(text):
                raise ValueError("unexpected end of JSON")
            ch = text[i]
            if ch == "{":
                return parse_object(i, path)
            if ch == "[":
                return parse_array(i, path)

            _value, end = raw_decode_at(i)
            spans.append((path, i, end))
            return end

        def parse_object(i: int, path: tuple[str, ...]) -> int:
            i += 1  # {
            i = skip_ws(i)
            if i < len(text) and text[i] == "}":
                return i + 1

            while True:
                i = skip_ws(i)
                if i >= len(text) or text[i] != '"':
                    raise ValueError("expected JSON object key")
                key, i = raw_decode_at(i)
                if not isinstance(key, str):
                    raise ValueError("expected JSON object key string")

                i = skip_ws(i)
                if i >= len(text) or text[i] != ":":
                    raise ValueError("expected ':' after JSON object key")
                i = parse_value(i + 1, path + (key,))
                i = skip_ws(i)

                if i < len(text) and text[i] == ",":
                    i += 1
                    continue
                if i < len(text) and text[i] == "}":
                    return i + 1
                raise ValueError("expected ',' or '}' in JSON object")

        def parse_array(i: int, path: tuple[str, ...]) -> int:
            i += 1  # [
            i = skip_ws(i)
            if i < len(text) and text[i] == "]":
                return i + 1

            index = 0
            while True:
                i = parse_value(i, path + (str(index),))
                index += 1
                i = skip_ws(i)

                if i < len(text) and text[i] == ",":
                    i += 1
                    continue
                if i < len(text) and text[i] == "]":
                    return i + 1
                raise ValueError("expected ',' or ']' in JSON array")

        try:
            end = parse_value(0, ())
            if skip_ws(end) != len(text):
                return None
            return spans
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def _generate_json_template(self, role_prefix: str, data: Any) -> str:
        """
        Generate a JSON Jinja2 template from parsed JSON data.

        All scalar values are replaced with Jinja expressions whose names are
        derived from the path, similar to TOML/YAML.

        Uses | to_json filter to preserve types (numbers, booleans, null).
        """

        def _walk(obj: Any, path: tuple[str, ...] = ()) -> Any:
            if isinstance(obj, dict):
                return {k: _walk(v, path + (str(k),)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v, path + (str(i),)) for i, v in enumerate(obj)]
            # scalar - use marker that will be replaced with to_json
            var_name = self.make_var_name(role_prefix, path)
            return f"__SCALAR__{var_name}__"

        templated = _walk(data)
        json_str = json.dumps(templated, indent=2, ensure_ascii=False)

        # Replace scalar markers with Jinja expressions using to_json filter
        # This preserves types (numbers stay numbers, booleans stay booleans)
        json_str = re.sub(
            r'"__SCALAR__([a-zA-Z_][a-zA-Z0-9_]*)__"',
            lambda m: self._json_value_expr(m.group(1)),
            json_str,
        )

        return json_str + "\n"

    def _generate_json_template_with_loops(
        self,
        role_prefix: str,
        data: Any,
        loop_paths: set[tuple[str, ...]],
        loop_candidates: list[LoopCandidate],
        path: tuple[str, ...] = (),
    ) -> str:
        """
        Generate a JSON Jinja2 template with for loops where appropriate.
        """

        def _walk(obj: Any, current_path: tuple[str, ...] = ()) -> Any:
            # Check if this path is a loop candidate
            if current_path in loop_paths:
                # Find the matching candidate
                candidate = next(c for c in loop_candidates if c.path == current_path)
                collection_var = self.make_var_name(role_prefix, candidate.path)
                item_var = candidate.loop_var

                if candidate.item_schema == "scalar":
                    # Simple list of scalars - use special marker that we'll replace
                    return f"__LOOP_SCALAR__{collection_var}__{item_var}__"
                elif candidate.item_schema in ("simple_dict", "nested"):
                    # List of dicts - use special marker
                    return f"__LOOP_DICT__{collection_var}__{item_var}__"

            if isinstance(obj, dict):
                return {k: _walk(v, current_path + (str(k),)) for k, v in obj.items()}
            if isinstance(obj, list):
                # Check if this list is a loop candidate
                if current_path in loop_paths:
                    # Already handled above
                    return _walk(obj, current_path)
                return [_walk(v, current_path + (str(i),)) for i, v in enumerate(obj)]

            # scalar - use marker to preserve type
            var_name = self.make_var_name(role_prefix, current_path)
            return f"__SCALAR__{var_name}__"

        templated = _walk(data, path)

        # Convert to JSON string
        json_str = json.dumps(templated, indent=2, ensure_ascii=False)

        # Replace scalar markers with Jinja expressions using to_json filter
        json_str = re.sub(
            r'"__SCALAR__([a-zA-Z_][a-zA-Z0-9_]*)__"',
            lambda m: self._json_value_expr(m.group(1)),
            json_str,
        )

        # Post-process to replace loop markers with actual Jinja loops (indent-aware)
        for candidate in loop_candidates:
            collection_var = self.make_var_name(role_prefix, candidate.path)
            item_var = candidate.loop_var

            if candidate.item_schema == "scalar":
                marker = f'"__LOOP_SCALAR__{collection_var}__{item_var}__"'
                json_str = self._replace_marker_with_pretty_loop(
                    json_str,
                    marker,
                    lambda base, cv=collection_var, iv=item_var, c=candidate: self._generate_json_scalar_loop(
                        cv, iv, c, base
                    ),
                )

            elif candidate.item_schema in ("simple_dict", "nested"):
                marker = f'"__LOOP_DICT__{collection_var}__{item_var}__"'
                json_str = self._replace_marker_with_pretty_loop(
                    json_str,
                    marker,
                    lambda base, cv=collection_var, iv=item_var, c=candidate: self._generate_json_dict_loop(
                        cv, iv, c, base
                    ),
                )

        return json_str + "\n"

    def _generate_json_scalar_loop(
        self,
        collection_var: str,
        item_var: str,
        candidate: LoopCandidate,
        base_indent: int,
    ) -> str:
        """Generate an indentation-preserving Jinja for-loop for a scalar JSON list."""
        if not candidate.items:
            return "[]"

        inner_indent = base_indent + self.JSON_INDENT
        inner = " " * inner_indent
        base = " " * base_indent

        # Put the `{% for %}` at the start of the first item line so we don't emit
        # a blank line between iterations under default Jinja whitespace settings.
        return (
            f"[\n"
            f"{{% for {item_var} in {collection_var} %}}"
            f"{inner}{{{{ {item_var} | to_json(ensure_ascii=False) }}}}"
            f"{{% if not loop.last %}},{{% endif %}}\n"
            f"{{% endfor %}}{base}]"
        )

    def _generate_json_dict_loop(
        self,
        collection_var: str,
        item_var: str,
        candidate: LoopCandidate,
        base_indent: int,
    ) -> str:
        """Generate an indentation-preserving Jinja for-loop for a list of dicts in JSON."""
        if not candidate.items:
            return "[]"

        # Get first item as template (preserve key order from the sample)
        sample_item = candidate.items[0]
        keys = [k for k in sample_item.keys() if k != "_key"]

        inner_indent = base_indent + self.JSON_INDENT  # list item indent
        field_indent = inner_indent + self.JSON_INDENT  # dict field indent
        inner = " " * inner_indent
        field = " " * field_indent
        base = " " * base_indent

        # Build a pretty dict body that matches json.dumps(indent=2) style.
        dict_lines: list[str] = [
            "{"
        ]  # first line has no indent; we prepend `inner` when emitting
        for i, key in enumerate(keys):
            comma = "," if i < len(keys) - 1 else ""
            dict_lines.append(
                f'{field}"{key}": '
                f"{{{{ {item_var}.{key} | to_json(ensure_ascii=False) }}}}{comma}"
            )
        # Comma between *items* goes after the closing brace.
        dict_lines.append(f"{inner}}}{{% if not loop.last %}},{{% endif %}}")
        dict_body = "\n".join(dict_lines)

        # Put the `{% for %}` at the start of the first item line to avoid blank lines.
        return (
            f"[\n"
            f"{{% for {item_var} in {collection_var} %}}{inner}{dict_body}\n"
            f"{{% endfor %}}{base}]"
        )
