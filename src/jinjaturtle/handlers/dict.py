from __future__ import annotations

from typing import Any

from . import BaseHandler


class DictLikeHandler(BaseHandler):
    """
    Base for TOML/YAML/JSON: nested dict/list structures.

    Subclasses control whether lists are flattened.
    """

    flatten_lists: bool = False  # override in subclasses

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        items: list[tuple[tuple[str, ...], Any]] = []

        def _walk(obj: Any, path: tuple[str, ...] = ()) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, path + (str(k),))
            elif isinstance(obj, list) and self.flatten_lists:
                for i, v in enumerate(obj):
                    _walk(v, path + (str(i),))
            else:
                items.append((path, obj))

        _walk(parsed)
        return items
