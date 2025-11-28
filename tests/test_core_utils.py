from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import jinjaturtle.core as core
from jinjaturtle.core import (
    detect_format,
    parse_config,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
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


def test_formats_match_expected_extensions():
    """
    Sanity check that format detection lines up with the filenames
    we’re using for the samples.
    """
    toml_path = SAMPLES_DIR / "tom.toml"
    ini_path = SAMPLES_DIR / "php.ini"
    xml_path = SAMPLES_DIR / "ossec.xml"

    fmt_toml, _ = parse_config(toml_path)
    fmt_ini, _ = parse_config(ini_path)
    fmt_xml, _ = parse_config(xml_path)

    assert fmt_toml == "toml"
    assert fmt_ini == "ini"
    assert fmt_xml == "xml"


def test_parse_config_unsupported_format(tmp_path: Path):
    """
    Hit the ValueError in parse_config when fmt is not a supported format.
    """
    cfg_path = tmp_path / "config.whatever"
    cfg_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        parse_config(cfg_path, fmt="bogus")


def test_generate_jinja2_template_type_and_format_errors():
    """
    Exercise the error branches in generate_jinja2_template:
      - toml with non-dict parsed
      - ini with non-ConfigParser parsed
      - yaml with wrong parsed type
      - json with wrong parsed type
      - completely unsupported fmt (with and without original_text)
    """
    # wrong type for TOML
    with pytest.raises(TypeError):
        generate_jinja2_template("toml", parsed="not a dict", role_prefix="role")

    # wrong type for INI
    with pytest.raises(TypeError):
        generate_jinja2_template(
            "ini", parsed={"not": "a configparser"}, role_prefix="role"
        )

    # wrong type for YAML
    with pytest.raises(TypeError):
        generate_jinja2_template("yaml", parsed=None, role_prefix="role")

    # wrong type for JSON
    with pytest.raises(TypeError):
        generate_jinja2_template("json", parsed=None, role_prefix="role")

    # unsupported format, no original_text
    with pytest.raises(ValueError):
        generate_jinja2_template("bogusfmt", parsed=None, role_prefix="role")

    # unsupported format, with original_text
    with pytest.raises(ValueError):
        generate_jinja2_template(
            "bogusfmt",
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
    ansible_yaml = generate_ansible_yaml("role", flat_items)
    data = yaml.safe_load(ansible_yaml)
    assert data["role_section_foo"] == "true"
    assert data["role_section_bar"] == "FALSE"


def test_fallback_str_representer_for_unknown_type():
    """
    Ensure that the _fallback_str_representer is used for objects that
    PyYAML doesn't know how to represent.
    """

    class Weird:
        def __str__(self) -> str:
            return "weird-value"

    data = {"foo": Weird()}

    dumped = yaml.dump(
        data,
        Dumper=core._TurtleDumper,
        sort_keys=False,
        default_flow_style=False,
    )

    # It should serialize without error, and the string form should appear.
    assert "weird-value" in dumped


def test_normalize_default_value_bool_inputs_are_stringified():
    """
    Real boolean values should be turned into quoted 'true'/'false' strings
    by _normalize_default_value via generate_ansible_yaml.
    """
    flat_items = [
        (("section", "flag_true"), True),
        (("section", "flag_false"), False),
    ]
    ansible_yaml = generate_ansible_yaml("role", flat_items)
    data = yaml.safe_load(ansible_yaml)

    assert data["role_section_flag_true"] == "true"
    assert data["role_section_flag_false"] == "false"


def test_flatten_config_unsupported_format():
    """
    Calling flatten_config with an unknown fmt should raise ValueError.
    """
    with pytest.raises(ValueError) as exc:
        flatten_config("bogusfmt", parsed=None)

    assert "Unsupported format" in str(exc.value)
