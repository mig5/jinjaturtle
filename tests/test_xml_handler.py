from __future__ import annotations

from pathlib import Path
import textwrap
import xml.etree.ElementTree as ET

import pytest
import yaml

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)
from jinjaturtle.handlers.xml import XmlHandler

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_xml_roundtrip_ossec_web_rules():
    xml_path = SAMPLES_DIR / "ossec.xml"
    assert xml_path.is_file(), f"Missing sample XML file: {xml_path}"

    fmt, parsed = parse_config(xml_path)
    assert fmt == "xml"

    flat_items = flatten_config(fmt, parsed)
    assert flat_items, "Expected at least one flattened item from XML sample"

    ansible_yaml = generate_ansible_yaml("ossec", flat_items)
    defaults = yaml.safe_load(ansible_yaml)

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
    template = generate_jinja2_template(
        fmt, parsed, "ossec", original_text=original_text
    )
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

    handler = XmlHandler()
    tmpl = handler._generate_xml_template_from_text("role", text)

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


def test_generate_jinja2_template_xml_type_error():
    """
    Wrong type for XML in XmlHandler.generate_jinja2_template should raise TypeError.
    """
    handler = XmlHandler()
    with pytest.raises(TypeError):
        handler.generate_jinja2_template(parsed="not an element", role_prefix="role")


def test_flatten_config_xml_type_error():
    """
    Wrong type for XML in flatten_config should raise TypeError.
    """
    with pytest.raises(TypeError):
        flatten_config("xml", parsed="not-an-element")


def test_generate_jinja2_template_xml_structural_fallback():
    """
    When original_text is not provided for XML, generate_jinja2_template should use
    the structural fallback path (ET.tostring + handler processing).
    """
    xml_text = textwrap.dedent(
        """\
        <root attr="1">
          <child>2</child>
          <node attr="x">text</node>
        </root>
        """
    )
    root = ET.fromstring(xml_text)

    tmpl = generate_jinja2_template("xml", parsed=root, role_prefix="role")

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
    handler = XmlHandler()
    prolog, body = handler._split_xml_prolog(text)
    assert prolog == text
    assert body == ""


def test_split_xml_prolog_unterminated_declaration():
    """
    Unterminated XML declaration should hit the 'end == -1' branch and
    treat the whole string as body.
    """
    text = "<?xml version='1.0'"
    handler = XmlHandler()
    prolog, body = handler._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_split_xml_prolog_unterminated_comment():
    """
    Unterminated comment should likewise hit its 'end == -1' branch.
    """
    text = "<!-- no end"
    handler = XmlHandler()
    prolog, body = handler._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_split_xml_prolog_unterminated_doctype():
    """
    Unterminated DOCTYPE should hit the DOCTYPE 'end == -1' branch.
    """
    text = "<!DOCTYPE foo"
    handler = XmlHandler()
    prolog, body = handler._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_split_xml_prolog_unexpected_content():
    """
    Non-XML content at the start should trigger the 'unexpected content'
    break and be returned entirely as body.
    """
    text = "garbage<root/>"
    handler = XmlHandler()
    prolog, body = handler._split_xml_prolog(text)
    assert prolog == ""
    assert body == text


def test_flatten_xml_text_with_attributes_uses_value_suffix():
    """
    When an element has both attributes and text, _flatten_xml should store
    the text at path + ('value',), not just path.
    """
    xml_text = "<root><node attr='x'>text</node></root>"
    root = ET.fromstring(xml_text)

    items = flatten_config("xml", root)

    # Attribute path: ("node", "@attr") -> "x"
    assert (("node", "@attr"), "x") in items

    # Text-with-attrs path: ("node", "value") -> "text"
    assert (("node", "value"), "text") in items
