from __future__ import annotations

from pathlib import Path

import pytest

from jinjaturtle.handlers.base import BaseHandler


def test_split_inline_comment_handles_quoted_hash():
    # The '#' inside quotes should not start a comment; the one outside should.
    text = " 'foo # not comment' # real"
    handler = BaseHandler()
    value, comment = handler._split_inline_comment(text, {"#"})
    assert "not comment" in value
    assert comment.strip() == "# real"


def test_base_handler_abstract_methods_raise_not_implemented(tmp_path: Path):
    """
    Ensure the abstract methods on BaseHandler all raise NotImplementedError.
    This covers the stub implementations.
    """
    handler = BaseHandler()
    dummy_path = tmp_path / "dummy.cfg"

    with pytest.raises(NotImplementedError):
        handler.parse(dummy_path)

    with pytest.raises(NotImplementedError):
        handler.flatten(object())

    with pytest.raises(NotImplementedError):
        handler.generate_jinja2_template(parsed=object(), role_prefix="role")
