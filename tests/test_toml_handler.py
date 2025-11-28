from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)
from jinjaturtle.handlers.toml import TomlHandler
import jinjaturtle.handlers.toml as toml_module

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_toml_sample_roundtrip():
    toml_path = SAMPLES_DIR / "tom.toml"
    assert toml_path.is_file(), f"Missing sample TOML file: {toml_path}"

    fmt, parsed = parse_config(toml_path)
    assert fmt == "toml"

    flat_items = flatten_config(fmt, parsed)
    assert flat_items

    ansible_yaml = generate_ansible_yaml("jinjaturtle", flat_items)
    defaults = yaml.safe_load(ansible_yaml)

    # defaults should be a non-empty dict
    assert isinstance(defaults, dict)
    assert defaults, "Expected non-empty defaults for TOML sample"

    # all keys should be lowercase, start with prefix, and have no spaces
    for key in defaults:
        assert key.startswith("jinjaturtle_")
        assert key == key.lower()
        assert " " not in key

    # template generation – **now with original_text**
    original_text = toml_path.read_text(encoding="utf-8")
    template = generate_jinja2_template(
        fmt, parsed, "jinjaturtle", original_text=original_text
    )
    assert isinstance(template, str)
    assert template.strip()

    # comments from the original file should now be preserved
    assert "# This is a TOML document" in template

    # each default variable name should appear in the template as a Jinja placeholder
    for var_name in defaults:
        assert (
            var_name in template
        ), f"Variable {var_name} not referenced in TOML template"


def test_parse_config_toml_missing_tomllib(monkeypatch):
    """
    Force tomllib to None to hit the RuntimeError branch when parsing TOML.
    """
    toml_path = SAMPLES_DIR / "tom.toml"

    # Simulate an environment without tomllib/tomli
    monkeypatch.setattr(toml_module, "tomllib", None)

    with pytest.raises(RuntimeError) as exc:
        parse_config(toml_path, fmt="toml")
    assert "tomllib/tomli is required" in str(exc.value)


def test_generate_jinja2_template_fallback_toml():
    """
    When original_text is not provided, generate_jinja2_template should use the
    structural fallback path for TOML configs.
    """
    parsed_toml = {
        "title": "Example",
        "server": {"port": 8080, "host": "127.0.0.1"},
        "logging": {
            "file": {"path": "/tmp/app.log"}
        },  # nested table to hit recursive walk
    }
    tmpl_toml = generate_jinja2_template("toml", parsed=parsed_toml, role_prefix="role")
    assert "[server]" in tmpl_toml
    assert "role_server_port" in tmpl_toml
    assert "[logging]" in tmpl_toml or "[logging.file]" in tmpl_toml


def test_generate_toml_template_from_text_edge_cases():
    # Cover CRLF newlines, lines without '=', empty keys, and inline tables
    # that both parse successfully and fail parsing.
    text = (
        "# comment\r\n"
        "[table]\r\n"
        "noequals\r\n"
        "   = 42\r\n"
        'inline_good = { name = "abc", value = 1 }\r\n'
        "inline_bad = { invalid = }\r\n"
    )
    handler = TomlHandler()
    tmpl = handler._generate_toml_template_from_text("role", text)

    # The good inline table should expand into two separate variables.
    assert "role_table_inline_good_name" in tmpl
    assert "role_table_inline_good_value" in tmpl
    # The bad inline table should fall back to scalar handling.
    assert "role_table_inline_bad" in tmpl
    # Ensure the lines without '=' / empty key were handled without exploding.
    assert "[table]" in tmpl
    assert "noequals" in tmpl
