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
    Boolean values are now preserved as booleans in YAML (not stringified).
    This supports proper type preservation for JSON and other formats.
    """
    flat_items = [
        (("section", "flag_true"), True),
        (("section", "flag_false"), False),
    ]
    ansible_yaml = generate_ansible_yaml("role", flat_items)
    data = yaml.safe_load(ansible_yaml)

    # Booleans are now preserved as booleans
    assert data["role_section_flag_true"] is True
    assert data["role_section_flag_false"] is False


def test_flatten_config_unsupported_format():
    """
    Calling flatten_config with an unknown fmt should raise ValueError.
    """
    with pytest.raises(ValueError) as exc:
        flatten_config("bogusfmt", parsed=None)

    assert "Unsupported format" in str(exc.value)


def test_make_var_name_collapses_adjacent_separators():
    """Regression: adjacent separators must not produce a forbidden ``__``.

    A source key such as ``log..level`` or ``cache--size`` previously sanitised
    to a name containing a double underscore (``..._log__level``). The output
    safety gate in safety.py rejects *any* ``__`` in a generated identifier
    (it is the gateway to Jinja2 SSTI gadgets), so emitting one made JinjaTurtle
    reject its own placeholder and abort generation on entirely benign config.
    make_var_name now collapses runs of underscores to a single ``_``.
    """
    for raw_key in ("log..level", "cache--size", "a...b", "x.-.y", "a..b--c"):
        name = make_var_name("role", ("main", raw_key))
        assert "__" not in name, f"{raw_key!r} produced {name!r}"
        # Still a valid Ansible/Jinja identifier.
        assert name.replace("_", "").isalnum() or name == "role"

    # The double-underscore-free collapse is stable and predictable.
    assert make_var_name("role", ("main", "log..level")) == "role_main_log_level"
    assert make_var_name("role", ("main", "cache--size")) == "role_main_cache_size"
    # A leading-digit-free prefix with its own repeated separators is collapsed too.
    assert make_var_name("My__Role", ("k",)) == "my_role_k"


def test_make_var_name_collapsed_names_pass_output_safety_gate():
    """The names make_var_name emits must be accepted by the safety gate.

    This binds the two modules together: whatever identifier make_var_name
    produces for a hostile-looking key must lex as the JinjaTurtle subset, so a
    real template built from it is not refused.
    """
    from jinjaturtle.safety import verify_jinja2_template_safe

    for raw_key in ("log..level", "cache--size", "a...b", "weird..key..name"):
        var = make_var_name("demo", ("section", raw_key))
        # Build the kind of expression a handler would emit for this variable.
        template = "{{ " + var + " }}"
        # Must not raise TemplateSafetyError.
        verify_jinja2_template_safe(template)
