from __future__ import annotations

from pathlib import Path

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
    assert flat_items, "Expected at least one flattened item from TOML sample"

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

    # template generation
    template = generate_template(fmt, parsed, "jinjaturtle")
    assert isinstance(template, str)
    assert template.strip(), "Template for TOML sample should not be empty"

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
    template = generate_template(fmt, parsed, "php")
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
