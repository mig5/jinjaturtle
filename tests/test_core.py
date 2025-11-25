from __future__ import annotations

from pathlib import Path
import configparser
import pytest
import yaml

import jinjaturtle.core as core
from jinjaturtle.core import (
    detect_format,
    parse_config,
    flatten_config,
    generate_defaults_yaml,
    generate_template,
    make_var_name,
)

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_make_var_name_basic():
    # simple sanity checks on the naming rules
    assert (
        make_var_name("jinjaturtle", ("somesection", "foo"))
        == "jinjaturtle_somesection_foo"
    )
    assert (
        make_var_name("JinjaTurtle", ("Other-Section", "some value"))
        == "jinjaturtle_other_section_some_value"
    )
    # no trailing underscores, all lowercase, no spaces
    name = make_var_name("MyRole", (" Section Name ", "Key-Name "))
    assert name == name.lower()
    assert " " not in name
    assert not name.endswith("_")


def test_make_var_name_empty_path_returns_prefix():
    # Cover the branch where there are no path components.
    assert make_var_name("MyRole", ()) == "myrole"


def test_detect_format_explicit_overrides_suffix(tmp_path: Path):
    # Explicit format should win over file suffix.
    cfg_path = tmp_path / "config.ini"
    cfg_path.write_text("[section]\nkey=value\n", encoding="utf-8")

    fmt = detect_format(cfg_path, explicit="toml")
    assert fmt == "toml"


def test_detect_format_fallback_ini(tmp_path: Path):
    # Unknown suffix should fall back to "ini".
    cfg_path = tmp_path / "weird.cnf"
    cfg_path.write_text("[section]\nkey=value\n", encoding="utf-8")

    fmt, parsed = parse_config(cfg_path)  # no explicit fmt
    assert fmt == "ini"
    # parsed should be an INI ConfigParser with our section/key
    flat = flatten_config(fmt, parsed)
    assert any(path == ("section", "key") for path, _ in flat)


def test_toml_sample_roundtrip():
    toml_path = SAMPLES_DIR / "tom.toml"
    assert toml_path.is_file(), f"Missing sample TOML file: {toml_path}"

    fmt, parsed = parse_config(toml_path)
    assert fmt == "toml"

    flat_items = flatten_config(fmt, parsed)
    assert flat_items

    defaults_yaml = generate_defaults_yaml("jinjaturtle", flat_items)
    defaults = yaml.safe_load(defaults_yaml)

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
    template = generate_template(
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


def test_formats_match_expected_extensions():
    """
    Sanity check that format detection lines up with the filenames
    we’re using for the samples.
    """
    toml_path = SAMPLES_DIR / "tom.toml"
    ini_path = SAMPLES_DIR / "php.ini"

    fmt_toml, _ = parse_config(toml_path)
    fmt_ini, _ = parse_config(ini_path)

    assert fmt_toml == "toml"
    assert fmt_ini == "ini"


def test_parse_config_toml_missing_tomllib(monkeypatch):
    """
    Force tomllib to None to hit the RuntimeError branch when parsing TOML.
    """
    toml_path = SAMPLES_DIR / "tom.toml"

    # Simulate an environment without tomllib/tomli
    monkeypatch.setattr(core, "tomllib", None)

    with pytest.raises(RuntimeError) as exc:
        core.parse_config(toml_path, fmt="toml")
    assert "tomllib/tomli is required" in str(exc.value)


def test_parse_config_unsupported_format(tmp_path: Path):
    """
    Hit the ValueError in parse_config when fmt is neither 'toml' nor 'ini'.
    """
    cfg_path = tmp_path / "config.whatever"
    cfg_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        parse_config(cfg_path, fmt="yaml")


def test_generate_template_type_and_format_errors():
    """
    Exercise the error branches in generate_template:
      - toml with non-dict parsed
      - ini with non-ConfigParser parsed
      - completely unsupported fmt
    """
    # wrong type for TOML
    with pytest.raises(TypeError):
        generate_template("toml", parsed="not a dict", role_prefix="role")

    # wrong type for INI
    with pytest.raises(TypeError):
        generate_template("ini", parsed={"not": "a configparser"}, role_prefix="role")

    # unsupported format
    with pytest.raises(ValueError):
        generate_template("yaml", parsed=None, role_prefix="role")

    # unsupported format even when original_text is provided
    with pytest.raises(ValueError):
        generate_template(
            "yaml",
            parsed=None,
            role_prefix="role",
            original_text="foo=bar",
        )


def test_normalize_default_value_true_false_strings():
    # 'true'/'false' strings should be preserved as strings and double-quoted in YAML.
    flat_items = [
        (("section", "foo"), "true"),
        (("section", "bar"), "FALSE"),
    ]
    defaults_yaml = generate_defaults_yaml("role", flat_items)
    data = yaml.safe_load(defaults_yaml)
    assert data["role_section_foo"] == "true"
    assert data["role_section_bar"] == "FALSE"


def test_split_inline_comment_handles_quoted_hash():
    # The '#' inside quotes should not start a comment; the one outside should.
    text = " 'foo # not comment' # real"
    value, comment = core._split_inline_comment(text, {"#"})
    assert "not comment" in value
    assert comment.strip() == "# real"


def test_generate_template_fallback_toml_and_ini():
    # When original_text is not provided, generate_template should use the
    # older fallback generators based on the parsed structures.
    parsed_toml = {
        "title": "Example",
        "server": {"port": 8080, "host": "127.0.0.1"},
        "logging": {
            "file": {"path": "/tmp/app.log"}
        },  # nested table to hit recursive walk
    }
    tmpl_toml = generate_template("toml", parsed=parsed_toml, role_prefix="role")
    assert "[server]" in tmpl_toml
    assert "role_server_port" in tmpl_toml
    assert "[logging]" in tmpl_toml or "[logging.file]" in tmpl_toml

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
    tmpl = core._generate_ini_template_from_text("role", text)
    # We don't care about exact formatting here, just that it runs and
    # produces some reasonable output.
    assert "[section]" in tmpl
    assert "role_section_key" in tmpl
    # The "noequals" line should be preserved as-is.
    assert "noequals" in tmpl
    # The "   = bare" line has no key and should be left untouched.
    assert "   = bare" in tmpl


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
    tmpl = core._generate_toml_template_from_text("role", text)
    # The good inline table should expand into two separate variables.
    assert "role_table_inline_good_name" in tmpl
    assert "role_table_inline_good_value" in tmpl
    # The bad inline table should fall back to scalar handling.
    assert "role_table_inline_bad" in tmpl
    # Ensure the lines without '=' / empty key were handled without exploding.
    assert "[table]" in tmpl
    assert "noequals" in tmpl
