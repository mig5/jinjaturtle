from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import DictLikeHandler


class JsonHandler(DictLikeHandler):
    fmt = "json"
    flatten_lists = True

    def parse(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def generate_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        if not isinstance(parsed, (dict, list)):
            raise TypeError("JSON parser result must be a dict or list")
        # Rebuild structurally
        return self._generate_json_template(role_prefix, parsed)

    def _generate_json_template(self, role_prefix: str, data: Any) -> str:
        """
        Generate a JSON Jinja2 template from parsed JSON data.

        All scalar values are replaced with Jinja expressions whose names are
        derived from the path, similar to TOML/YAML.
        """

        def _walk(obj: Any, path: tuple[str, ...] = ()) -> Any:
            if isinstance(obj, dict):
                return {k: _walk(v, path + (str(k),)) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(v, path + (str(i),)) for i, v in enumerate(obj)]
            # scalar
            var_name = self.make_var_name(role_prefix, path)
            return f"{{{{ {var_name} }}}}"

        templated = _walk(data)
        return json.dumps(templated, indent=2, ensure_ascii=False) + "\n"
