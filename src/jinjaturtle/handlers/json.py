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
        """Original scalar-only template generation."""
        if not isinstance(parsed, (dict, list)):
            raise TypeError("JSON parser result must be a dict or list")
        # As before: ignore original_text and rebuild structurally
        return self._generate_json_template(role_prefix, parsed)

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

    def _generate_json_template(self, role_prefix: str, data: Any) -> str:
        """
        Generate a JSON Jinja2 template from parsed JSON data.

        All scalar values are replaced with Jinja expressions whose names are
        derived from the path, similar to TOML/YAML.

        Uses | tojson filter to preserve types (numbers, booleans, null).
        """

        def _walk(obj: Any, path: tuple[str, ...] = ()) -> Any:
            if isinstance(obj, dict):
                return {k: _walk(v, path + (str(k),)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v, path + (str(i),)) for i, v in enumerate(obj)]
            # scalar - use marker that will be replaced with tojson
            var_name = self.make_var_name(role_prefix, path)
            return f"__SCALAR__{var_name}__"

        templated = _walk(data)
        json_str = json.dumps(templated, indent=2, ensure_ascii=False)

        # Replace scalar markers with Jinja expressions using tojson filter
        # This preserves types (numbers stay numbers, booleans stay booleans)
        json_str = re.sub(
            r'"__SCALAR__([a-zA-Z_][a-zA-Z0-9_]*)__"', r"{{ \1 | tojson }}", json_str
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

        # Replace scalar markers with Jinja expressions using tojson filter
        json_str = re.sub(
            r'"__SCALAR__([a-zA-Z_][a-zA-Z0-9_]*)__"', r"{{ \1 | tojson }}", json_str
        )

        # Post-process to replace loop markers with actual Jinja loops
        for candidate in loop_candidates:
            collection_var = self.make_var_name(role_prefix, candidate.path)
            item_var = candidate.loop_var

            if candidate.item_schema == "scalar":
                # Replace scalar loop marker with Jinja for loop
                marker = f'"__LOOP_SCALAR__{collection_var}__{item_var}__"'
                replacement = self._generate_json_scalar_loop(
                    collection_var, item_var, candidate
                )
                json_str = json_str.replace(marker, replacement)

            elif candidate.item_schema in ("simple_dict", "nested"):
                # Replace dict loop marker with Jinja for loop
                marker = f'"__LOOP_DICT__{collection_var}__{item_var}__"'
                replacement = self._generate_json_dict_loop(
                    collection_var, item_var, candidate
                )
                json_str = json_str.replace(marker, replacement)

        return json_str + "\n"

    def _generate_json_scalar_loop(
        self, collection_var: str, item_var: str, candidate: LoopCandidate
    ) -> str:
        """Generate a Jinja for loop for a scalar list in JSON."""
        # Use tojson filter to properly handle strings (quotes them) and other types
        # Include array brackets around the loop
        return (
            f"[{{% for {item_var} in {collection_var} %}}"
            f"{{{{ {item_var} | tojson }}}}"
            f"{{% if not loop.last %}}, {{% endif %}}"
            f"{{% endfor %}}]"
        )

    def _generate_json_dict_loop(
        self, collection_var: str, item_var: str, candidate: LoopCandidate
    ) -> str:
        """Generate a Jinja for loop for a dict list in JSON."""
        if not candidate.items:
            return "[]"

        # Get first item as template
        sample_item = candidate.items[0]

        # Build the dict template - use tojson for all values to handle types correctly
        fields = []
        for key, value in sample_item.items():
            if key == "_key":
                continue
            # Use tojson filter to properly serialize all types (strings, numbers, booleans)
            fields.append(f'"{key}": {{{{ {item_var}.{key} | tojson }}}}')

        dict_template = "{" + ", ".join(fields) + "}"

        return (
            f"{{% for {item_var} in {collection_var} %}}"
            f"{dict_template}"
            f"{{% if not loop.last %}}, {{% endif %}}"
            f"{{% endfor %}}"
        )
