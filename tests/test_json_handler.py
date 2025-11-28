from __future__ import annotations

from pathlib import Path

import json
import pytest
import yaml

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_ansible_yaml,
)
from jinjaturtle.handlers.json import JsonHandler

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_json_roundtrip():
    json_path = SAMPLES_DIR / "foo.json"
    assert json_path.is_file(), f"Missing sample JSON file: {json_path}"

    fmt, parsed = parse_config(json_path)
    assert fmt == "json"

    flat_items = flatten_config(fmt, parsed)
    ansible_yaml = generate_ansible_yaml("foobar", flat_items)
    defaults = yaml.safe_load(ansible_yaml)

    # Defaults: nested keys and list indices
    assert defaults["foobar_foo"] == "bar"
    assert defaults["foobar_nested_a"] == 1
    # Bool normalized to string "true"
    assert defaults["foobar_nested_b"] == "true"
    assert defaults["foobar_list_0"] == 10
    assert defaults["foobar_list_1"] == 20

    # Template generation is done via JsonHandler.generate_jinja2_template; we just
    # make sure it produces a structure with the expected placeholders.
    handler = JsonHandler()
    templated = json.loads(
        handler.generate_jinja2_template(parsed, role_prefix="foobar")
    )

    assert templated["foo"] == "{{ foobar_foo }}"
    assert "foobar_nested_a" in str(templated)
    assert "foobar_nested_b" in str(templated)
    assert "foobar_list_0" in str(templated)
    assert "foobar_list_1" in str(templated)


def test_generate_jinja2_template_json_type_error():
    """
    Wrong type for JSON in JsonHandler.generate_jinja2_template should raise TypeError.
    """
    handler = JsonHandler()
    with pytest.raises(TypeError):
        handler.generate_jinja2_template(parsed="not a dict", role_prefix="role")
