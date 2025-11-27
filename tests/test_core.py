from __future__ import annotations

from defusedxml import ElementTree as ET
from pathlib import Path
import configparser
import pytest
import textwrap
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
    xml_path = SAMPLES_DIR / "ossec.xml"

    fmt_toml, _ = parse_config(toml_path)
    fmt_ini, _ = parse_config(ini_path)
    fmt_xml, _ = parse_config(xml_path)

    assert fmt_toml == "toml"
    assert fmt_ini == "ini"
    assert fmt_xml == "xml"


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
    Hit the ValueError in parse_config when fmt is not a supported format.
    """
    cfg_path = tmp_path / "config.whatever"
    cfg_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        parse_config(cfg_path, fmt="bogus")


def test_generate_template_type_and_format_errors():
    """
    Exercise the error branches in generate_template:
      - toml with non-dict parsed
      - ini with non-ConfigParser parsed
      - yaml with wrong parsed type
      - completely unsupported fmt (with and without original_text)
    """
    # wrong type for TOML
    with pytest.raises(TypeError):
        generate_template("toml", parsed="not a dict", role_prefix="role")

    # wrong type for INI
    with pytest.raises(TypeError):
        generate_template("ini", parsed={"not": "a configparser"}, role_prefix="role")

    # wrong type for YAML
    with pytest.raises(TypeError):
        generate_template("yaml", parsed=None, role_prefix="role")

    # wrong type for JSON
    with pytest.raises(TypeError):
        generate_template("json", parsed=None, role_prefix="role")

    # unsupported format, no original_text
    with pytest.raises(ValueError):
        generate_template("bogusfmt", parsed=None, role_prefix="role")

    # unsupported format, with original_text
    with pytest.raises(ValueError):
        generate_template(
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


def test_yaml_roundtrip_with_list_and_comment(tmp_path: Path):
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


def test_json_roundtrip(tmp_path: Path):
    json_path = SAMPLES_DIR / "foo.json"
    assert json_path.is_file(), f"Missing sample JSON file: {json_path}"

    fmt, parsed = parse_config(json_path)
    assert fmt == "json"

    flat_items = flatten_config(fmt, parsed)
    defaults_yaml = generate_defaults_yaml("foobar", flat_items)
    defaults = yaml.safe_load(defaults_yaml)

    # Defaults: nested keys and list indices
    assert defaults["foobar_foo"] == "bar"
    assert defaults["foobar_nested_a"] == 1
    # Bool normalized to string "true"
    assert defaults["foobar_nested_b"] == "true"
    assert defaults["foobar_list_0"] == 10
    assert defaults["foobar_list_1"] == 20

    # Template generation (JSON has no comments, so we just rebuild)
    template = generate_template(fmt, parsed, "foobar")

    assert '"foo": "{{ foobar_foo }}"' in template
    assert "foobar_nested_a" in template
    assert "foobar_nested_b" in template
    assert "foobar_list_0" in template
    assert "foobar_list_1" in template


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

    tmpl = core._generate_yaml_template_from_text("role", text)

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
    the structural fallback path (yaml.safe_dump + _generate_yaml_template_from_text).
    """
    parsed = {"outer": {"inner": "val"}}

    tmpl = generate_template("yaml", parsed=parsed, role_prefix="role")

    # We don't care about exact formatting, just that the expected variable
    # name shows up, proving we went through the structural path.
    assert "role_outer_inner" in tmpl


def test_generate_template_json_type_error():
    """
    Wrong type for JSON in generate_template should raise TypeError.
    """
    with pytest.raises(TypeError):
        generate_template("json", parsed="not a dict", role_prefix="role")


def test_fallback_str_representer_for_unknown_type():
    """
    Ensure that the _fallback_str_representer is used for objects that
    PyYAML doesn't know how to represent.
    """

    class Weird:
        def __str__(self) -> str:
            return "weird-value"

    data = {"foo": Weird()}

    # This will exercise _fallback_str_representer, because Weird has no
    # dedicated representer and _TurtleDumper registers our fallback for None.
    dumped = yaml.dump(
        data,
        Dumper=core._TurtleDumper,
        sort_keys=False,
        default_flow_style=False,
    )

    # It should serialize without error, and the string form should appear.
    assert "weird-value" in dumped


def test_xml_roundtrip_ossec_web_rules():
    xml_path = SAMPLES_DIR / "ossec.xml"
    assert xml_path.is_file(), f"Missing sample XML file: {xml_path}"

    fmt, parsed = parse_config(xml_path)
    assert fmt == "xml"

    flat_items = flatten_config(fmt, parsed)
    assert flat_items, "Expected at least one flattened item from XML sample"

    defaults_yaml = generate_defaults_yaml("ossec", flat_items)
    defaults = yaml.safe_load(defaults_yaml)

    # defaults should be a non-empty dict
    assert isinstance(defaults, dict)
    assert defaults, "Expected non-empty defaults for XML sample"

    # all keys should be lowercase, start with prefix, and have no spaces
    for key in defaults:
        assert key.startswith("ossec_")
        assert key == key.lower()
        assert " " not in key

    # Root <group name="web,accesslog,"> attribute should flatten to ossec_name
    assert defaults["ossec_name"] == "web,accesslog,"

    # There should be at least one default for rule id="31100"
    id_keys = [k for k, v in defaults.items() if v == "31100"]
    assert id_keys, "Expected to find a default for rule id 31100"

    # At least one of them should be the rule *id* attribute
    assert any(
        key.startswith("ossec_rule_") and key.endswith("_id") for key in id_keys
    ), f"Expected at least one *_id var for value 31100, got: {id_keys}"

    # Template generation (preserving comments)
    original_text = xml_path.read_text(encoding="utf-8")
    template = generate_template(fmt, parsed, "ossec", original_text=original_text)
    assert isinstance(template, str)
    assert template.strip(), "Template for XML sample should not be empty"

    # Top-of-file and mid-file comments should be preserved
    assert "Official Web access rules for OSSEC." in template
    assert "Rules to ignore crawlers" in template

    # Each default variable name should appear in the template as a Jinja placeholder
    for var_name in defaults:
        assert (
            var_name in template
        ), f"Variable {var_name} not referenced in XML template"


def test_generate_xml_template_from_text_edge_cases():
    """
    Exercise XML text edge cases:
      - XML declaration and DOCTYPE in prolog
      - top-level and inner comments
      - repeated child elements (indexing)
      - attributes and text content
    """
    text = textwrap.dedent(
        """\
        <?xml version="1.0"?>
        <!-- top comment -->
        <!DOCTYPE something>
        <root attr="1">
          <!-- inner comment -->
          <child attr="2">text</child>
          <child>other</child>
        </root>
        """
    )

    tmpl = core._generate_xml_template_from_text("role", text)

    # Prolog and comments preserved
    assert "<?xml version" in tmpl
    assert "top comment" in tmpl
    assert "inner comment" in tmpl

    # Root attribute becomes a variable (path ("@attr",) -> role_attr)
    assert "role_attr" in tmpl

    # Repeated <child> elements should be indexed in both attr and text
    assert "role_child_0_attr" in tmpl
    assert "role_child_0" in tmpl
    assert "role_child_1" in tmpl


def test_generate_template_xml_type_error():
    """
    Wrong type for XML in generate_template should raise TypeError.
    """
    with pytest.raises(TypeError):
        generate_template("xml", parsed="not an element", role_prefix="role")


def test_flatten_config_xml_type_error():
    """
    Wrong type for XML in flatten_config should raise TypeError.
    """
    with pytest.raises(TypeError):
        flatten_config("xml", parsed="not-an-element")


def test_generate_template_xml_structural_fallback():
    """
    When original_text is not provided for XML, generate_template should use
    the structural fallback path (ET.tostring + _generate_xml_template_from_text).
    """
    xml_text = textwrap.dedent(
        """\
        <root attr="1">
          <child>2</child>
          <node attr="x">text</node>
        </root>
        """
    )
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=False))
    root = ET.fromstring(xml_text, parser=parser)

    tmpl = generate_template("xml", parsed=root, role_prefix="role")

    # Root attribute path ("@attr",) -> role_attr
    assert "role_attr" in tmpl

    # Simple child element text ("child",) -> role_child
    assert "role_child" in tmpl

    # Element with both attr and text:
    #  - attr -> ("node", "@attr")  -> role_node_attr
    #  - text -> ("node", "value")  -> role_node_value
    assert "role_node_attr" in tmpl
    assert "role_node_value" in tmpl


def test_split_xml_prolog_only_whitespace():
    """
    Whitespace-only input: prolog is the whitespace, body is empty.
    Exercises the 'if i >= n: break' path.
    """
    text = "   \n\t"
    prolog, body = core._split_xml_prolog(text)
    assert prolog == text
    assert body == ""


def test_split_xml_prolog_unterminated_declaration():
    """
    Unterminated XML declaration should hit the 'end == -1' branch and
    treat the whole string as body.
    """
    text = "<?xml version='1.0'"
    prolog, body = core._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_split_xml_prolog_unterminated_comment():
    """
    Unterminated comment should likewise hit its 'end == -1' branch.
    """
    text = "<!-- no end"
    prolog, body = core._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_split_xml_prolog_unterminated_doctype():
    """
    Unterminated DOCTYPE should hit the DOCTYPE 'end == -1' branch.
    """
    text = "<!DOCTYPE foo"
    prolog, body = core._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_split_xml_prolog_unexpected_content():
    """
    Non-XML content at the start should trigger the 'unexpected content'
    break and be returned entirely as body.
    """
    text = "garbage<root/>"
    prolog, body = core._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_flatten_xml_text_with_attributes_uses_value_suffix():
    """
    When an element has both attributes and text, _flatten_xml should store
    the text at path + ('value',), not just path.
    """
    xml_text = "<root><node attr='x'>text</node></root>"
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=False))
    root = ET.fromstring(xml_text, parser=parser)

    items = flatten_config("xml", root)

    # Attribute path: ("node", "@attr") -> "x"
    assert (("node", "@attr"), "x") in items

    # Text-with-attrs path: ("node", "value") -> "text"
    assert (("node", "value"), "text") in items
