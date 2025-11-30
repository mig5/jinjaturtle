from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_ansible_yaml,
    analyze_loops,
    generate_jinja2_template,
)
from jinjaturtle.handlers.json import JsonHandler

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_json_roundtrip():
    json_path = SAMPLES_DIR / "foo.json"
    assert json_path.is_file(), f"Missing sample JSON file: {json_path}"

    fmt, parsed = parse_config(json_path)
    assert fmt == "json"

    # With loop detection
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)
    ansible_yaml = generate_ansible_yaml("foobar", flat_items, loop_candidates)
    defaults = yaml.safe_load(ansible_yaml)

    # Defaults: nested keys
    assert defaults["foobar_foo"] == "bar"
    assert defaults["foobar_nested_a"] == 1
    # Booleans are now preserved as booleans (not stringified)
    assert defaults["foobar_nested_b"] is True
    # List should be a list (not flattened to scalars)
    assert defaults["foobar_list"] == [10, 20]

    # Template generation with loops
    template = generate_jinja2_template("json", parsed, "foobar", None, loop_candidates)

    # Template should use | tojson for type preservation
    assert "{{ foobar_foo | tojson }}" in template
    assert "{{ foobar_nested_a | tojson }}" in template
    assert "{{ foobar_nested_b | tojson }}" in template

    # List should use loop (not scalar indices)
    assert "{% for" in template
    assert "foobar_list" in template
    # Should NOT have scalar indices
    assert "foobar_list_0" not in template
    assert "foobar_list_1" not in template


def test_generate_jinja2_template_json_type_error():
    """
    Wrong type for JSON in JsonHandler.generate_jinja2_template should raise TypeError.
    """
    handler = JsonHandler()
    with pytest.raises(TypeError):
        handler.generate_jinja2_template(parsed="not a dict", role_prefix="role")
