from __future__ import annotations

from pathlib import Path
import textwrap

import yaml

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_defaults_yaml,
    generate_template,
)
from jinjaturtle.handlers.yaml import YamlHandler

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_yaml_roundtrip_with_list_and_comment():
    yaml_path = SAMPLES_DIR / "bar.yaml"
    assert yaml_path.is_file(), f"Missing sample YAML file: {yaml_path}"

    fmt, parsed = parse_config(yaml_path)
    assert fmt == "yaml"

    flat_items = flatten_config(fmt, parsed)
    defaults_yaml = generate_defaults_yaml("foobar", flat_items)
    defaults = yaml.safe_load(defaults_yaml)

    # Defaults: keys are flattened with indices
    assert defaults["foobar_foo"] == "bar"
    assert defaults["foobar_blah_0"] == "something"
    assert defaults["foobar_blah_1"] == "else"

    # Template generation (preserving comments)
    original_text = yaml_path.read_text(encoding="utf-8")
    template = generate_template(fmt, parsed, "foobar", original_text=original_text)

    # Comment preserved
    assert "# Top comment" in template

    # Scalar replacement
    assert "foo:" in template
    assert "foobar_foo" in template

    # List items use indexed vars, not "item"
    assert "foobar_blah_0" in template
    assert "foobar_blah_1" in template
    assert "{{ foobar_blah }}" not in template
    assert "foobar_blah_item" not in template


def test_generate_yaml_template_from_text_edge_cases():
    """
    Exercise YAML text edge cases:
      - indentation dedent (stack pop)
      - empty key before ':'
      - quoted and unquoted list items
    """
    text = textwrap.dedent(
        """
        root:
          child: 1
        other: 2
          : 3
        list:
          - "quoted"
          - unquoted
        """
    )

    handler = YamlHandler()
    tmpl = handler._generate_yaml_template_from_text("role", text)

    # Dedent from "root -> child" back to "other" exercises the stack-pop path.
    # Just check the expected variable names appear.
    assert "role_root_child" in tmpl
    assert "role_other" in tmpl

    # The weird "  : 3" line has no key and should be left untouched.
    assert "  : 3" in tmpl

    # The list should generate indexed variables for each item.
    # First item is quoted (use_quotes=True), second is unquoted.
    assert "role_list_0" in tmpl
    assert "role_list_1" in tmpl


def test_generate_template_yaml_structural_fallback():
    """
    When original_text is not provided for YAML, generate_template should use
    the structural fallback path (yaml.safe_dump + handler processing).
    """
    parsed = {"outer": {"inner": "val"}}

    tmpl = generate_template("yaml", parsed=parsed, role_prefix="role")

    # We don't care about exact formatting, just that the expected variable
    # name shows up, proving we went through the structural path.
    assert "role_outer_inner" in tmpl
