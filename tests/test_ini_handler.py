from __future__ import annotations

from pathlib import Path
import configparser
import pytest
import yaml

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_defaults_yaml,
    generate_template,
)
from jinjaturtle.handlers.ini import IniHandler

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_ini_php_sample_roundtrip():
    ini_path = SAMPLES_DIR / "php.ini"
    assert ini_path.is_file(), f"Missing sample INI file: {ini_path}"

    fmt, parsed = parse_config(ini_path)
    assert fmt == "ini"

    flat_items = flatten_config(fmt, parsed)
    assert flat_items, "Expected at least one flattened item from php.ini sample"

    defaults_yaml = generate_defaults_yaml("php", flat_items)
    defaults = yaml.safe_load(defaults_yaml)

    # defaults should be a non-empty dict
    assert isinstance(defaults, dict)
    assert defaults, "Expected non-empty defaults for php.ini sample"

    # all keys should be lowercase, start with prefix, and have no spaces
    for key in defaults:
        assert key.startswith("php_")
        assert key == key.lower()
        assert " " not in key

    # template generation
    original_text = ini_path.read_text(encoding="utf-8")
    template = generate_template(fmt, parsed, "php", original_text=original_text)
    assert "; About this file" in template
    assert isinstance(template, str)
    assert template.strip(), "Template for php.ini sample should not be empty"

    # each default variable name should appear in the template as a Jinja placeholder
    for var_name in defaults:
        assert (
            var_name in template
        ), f"Variable {var_name} not referenced in INI template"


def test_generate_template_fallback_ini():
    """
    When original_text is not provided, generate_template should use the
    structural fallback path for INI configs.
    """
    parser = configparser.ConfigParser()
    # foo is quoted in the INI text to hit the "preserve quotes" branch
    parser["section"] = {"foo": '"bar"', "num": "42"}

    tmpl_ini = generate_template("ini", parsed=parser, role_prefix="role")
    assert "[section]" in tmpl_ini
    assert "role_section_foo" in tmpl_ini
    assert '"{{ role_section_foo }}"' in tmpl_ini  # came from quoted INI value


def test_generate_ini_template_from_text_edge_cases():
    # Cover CRLF newlines, lines without '=', and lines with no key before '='.
    text = "[section]\r\nkey=value\r\nnoequals\r\n   = bare\r\n"
    handler = IniHandler()
    tmpl = handler._generate_ini_template_from_text("role", text)

    # We don't care about exact formatting here, just that it runs and
    # produces some reasonable output.
    assert "[section]" in tmpl
    assert "role_section_key" in tmpl
    # The "noequals" line should be preserved as-is.
    assert "noequals" in tmpl
    # The "   = bare" line has no key and should be left untouched.
    assert "   = bare" in tmpl


def test_ini_handler_flatten_type_error():
    """
    Passing a non-ConfigParser into IniHandler.flatten should raise TypeError.
    """
    handler = IniHandler()
    with pytest.raises(TypeError):
        handler.flatten(parsed={"not": "a configparser"})
