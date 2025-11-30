"""
Roundtrip tests: Generate config → template/YAML → regenerate config → compare.

These tests verify that:
1. Generated Jinja2 template + Ansible YAML can reproduce the original config
2. The regenerated config is semantically equivalent (allowing whitespace differences)
3. No data loss occurs during the template generation process

This is the ultimate validation - if the roundtrip works, the templates are correct.
"""

from __future__ import annotations

import json
import yaml
from pathlib import Path
from typing import Any
from jinja2 import Environment, StrictUndefined

import pytest

from jinjaturtle.core import (
    parse_config,
    analyze_loops,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)


def render_template(template: str, variables: dict[str, Any]) -> str:
    """Render a Jinja2 template with variables."""
    env = Environment(undefined=StrictUndefined)
    jinja_template = env.from_string(template)
    return jinja_template.render(variables)


class TestRoundtripJSON:
    """Roundtrip tests for JSON files."""

    def test_foo_json_roundtrip(self):
        """Test foo.json can be perfectly regenerated from template."""
        samples_dir = Path(__file__).parent / "samples"
        json_file = samples_dir / "foo.json"

        if not json_file.exists():
            pytest.skip("foo.json not found")

        # Read original
        original_text = json_file.read_text()
        original_data = json.loads(original_text)

        # Generate template and YAML
        fmt, parsed = parse_config(json_file)
        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(fmt, parsed, "test", None, loop_candidates)

        # Load variables from YAML
        variables = yaml.safe_load(ansible_yaml)

        # Render template
        regenerated_text = render_template(template, variables)
        regenerated_data = json.loads(regenerated_text)

        # Compare data structures (should match exactly)
        assert regenerated_data == original_data, (
            f"Regenerated JSON differs from original\n"
            f"Original: {json.dumps(original_data, indent=2, sort_keys=True)}\n"
            f"Regenerated: {json.dumps(regenerated_data, indent=2, sort_keys=True)}"
        )

    def test_json_all_types_roundtrip(self):
        """Test JSON with all data types roundtrips perfectly."""
        json_text = """
        {
          "string": "value",
          "number": 42,
          "float": 3.14,
          "boolean": true,
          "false_val": false,
          "null_value": null,
          "array": [1, 2, 3],
          "object": {
            "nested": "data"
          }
        }
        """

        original_data = json.loads(json_text)

        # Generate template and YAML
        loop_candidates = analyze_loops("json", original_data)
        flat_items = flatten_config("json", original_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", original_data, "test", None, loop_candidates
        )

        # Render template
        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = json.loads(regenerated_text)

        # Should match exactly
        assert regenerated_data == original_data


class TestRoundtripYAML:
    """Roundtrip tests for YAML files."""

    def test_bar_yaml_roundtrip(self):
        """Test bar.yaml can be regenerated from template."""
        samples_dir = Path(__file__).parent / "samples"
        yaml_file = samples_dir / "bar.yaml"

        if not yaml_file.exists():
            pytest.skip("bar.yaml not found")

        # Read original
        original_text = yaml_file.read_text()
        original_data = yaml.safe_load(original_text)

        # Generate template and YAML
        fmt, parsed = parse_config(yaml_file)
        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            fmt, parsed, "test", original_text, loop_candidates
        )

        # Load variables from YAML
        variables = yaml.safe_load(ansible_yaml)

        # Render template
        regenerated_text = render_template(template, variables)
        regenerated_data = yaml.safe_load(regenerated_text)

        # Compare data structures
        assert regenerated_data == original_data, (
            f"Regenerated YAML differs from original\n"
            f"Original: {original_data}\n"
            f"Regenerated: {regenerated_data}"
        )

    def test_yaml_with_lists_roundtrip(self):
        """Test YAML with various list structures."""
        yaml_text = """
        name: myapp
        simple_list:
          - item1
          - item2
          - item3
        list_of_dicts:
          - name: first
            value: 1
          - name: second
            value: 2
        nested:
          inner_list:
            - a
            - b
        """

        original_data = yaml.safe_load(yaml_text)

        # Generate template and YAML
        loop_candidates = analyze_loops("yaml", original_data)
        flat_items = flatten_config("yaml", original_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "yaml", original_data, "test", yaml_text, loop_candidates
        )

        # Render template
        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = yaml.safe_load(regenerated_text)

        # Compare
        assert regenerated_data == original_data


class TestRoundtripTOML:
    """Roundtrip tests for TOML files."""

    def test_tom_toml_roundtrip(self):
        """Test tom.toml can be regenerated from template."""
        samples_dir = Path(__file__).parent / "samples"
        toml_file = samples_dir / "tom.toml"

        if not toml_file.exists():
            pytest.skip("tom.toml not found")

        # Read original
        original_text = toml_file.read_text()
        import tomllib

        original_data = tomllib.loads(original_text)

        # Generate template and YAML
        fmt, parsed = parse_config(toml_file)
        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            fmt, parsed, "test", original_text, loop_candidates
        )

        # Load variables from YAML
        variables = yaml.safe_load(ansible_yaml)

        # Render template
        regenerated_text = render_template(template, variables)
        regenerated_data = tomllib.loads(regenerated_text)

        # Compare data structures
        # Note: TOML datetime objects need special handling
        assert _compare_toml_data(regenerated_data, original_data), (
            f"Regenerated TOML differs from original\n"
            f"Original: {original_data}\n"
            f"Regenerated: {regenerated_data}"
        )

    def test_toml_with_arrays_roundtrip(self):
        """Test TOML with inline arrays and array-of-tables."""
        toml_text = """
        name = "test"
        ports = [8080, 8081, 8082]
        
        [[database]]
        host = "db1.example.com"
        port = 5432
        
        [[database]]
        host = "db2.example.com"
        port = 5433
        """

        import tomllib

        original_data = tomllib.loads(toml_text)

        # Generate template and YAML
        loop_candidates = analyze_loops("toml", original_data)
        flat_items = flatten_config("toml", original_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "toml", original_data, "test", toml_text, loop_candidates
        )

        # Render template
        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = tomllib.loads(regenerated_text)

        # Compare
        assert regenerated_data == original_data


class TestRoundtripXML:
    """Roundtrip tests for XML files."""

    def test_xml_simple_roundtrip(self):
        """Test simple XML can be regenerated."""
        xml_text = """<?xml version="1.0"?>
<config>
  <name>test</name>
  <port>8080</port>
  <server>server1</server>
  <server>server2</server>
  <server>server3</server>
</config>
"""

        import xml.etree.ElementTree as ET

        original_root = ET.fromstring(xml_text)

        # Generate template and YAML
        fmt = "xml"
        loop_candidates = analyze_loops(fmt, original_root)
        flat_items = flatten_config(fmt, original_root, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            fmt, original_root, "test", xml_text, loop_candidates
        )

        # Render template
        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)

        # Parse regenerated XML
        regenerated_root = ET.fromstring(regenerated_text)

        # Compare XML structures (ignore insignificant whitespace)
        assert _xml_elements_equal(
            original_root, regenerated_root, ignore_whitespace=True
        ), (
            f"Regenerated XML differs from original\n"
            f"Original: {ET.tostring(original_root, encoding='unicode')}\n"
            f"Regenerated: {ET.tostring(regenerated_root, encoding='unicode')}"
        )

    def test_ossec_xml_roundtrip(self):
        """Test ossec.xml (complex real-world XML) roundtrip."""
        samples_dir = Path(__file__).parent / "samples"
        xml_file = samples_dir / "ossec.xml"

        if not xml_file.exists():
            pytest.skip("ossec.xml not found")

        # Read original
        original_text = xml_file.read_text()
        import xml.etree.ElementTree as ET

        original_root = ET.fromstring(original_text)

        # Generate template and YAML
        fmt, parsed = parse_config(xml_file)
        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            fmt, parsed, "test", original_text, loop_candidates
        )

        # Load variables and render
        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)

        # Parse regenerated
        regenerated_root = ET.fromstring(regenerated_text)

        # Compare - for complex XML, we compare structure not exact text
        assert _xml_elements_equal(
            original_root, regenerated_root, ignore_whitespace=True
        )


class TestRoundtripINI:
    """Roundtrip tests for INI files."""

    def test_ini_simple_roundtrip(self):
        """Test simple INI can be regenerated."""
        ini_text = """[section1]
key1 = value1
key2 = value2

[section2]
key3 = value3
"""

        from configparser import ConfigParser

        original_config = ConfigParser()
        original_config.read_string(ini_text)

        # Generate template and YAML
        fmt = "ini"
        loop_candidates = analyze_loops(fmt, original_config)
        flat_items = flatten_config(fmt, original_config, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            fmt, original_config, "test", ini_text, loop_candidates
        )

        # Render template
        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)

        # Parse regenerated
        regenerated_config = ConfigParser()
        regenerated_config.read_string(regenerated_text)

        # Compare
        assert _ini_configs_equal(original_config, regenerated_config)


class TestRoundtripEdgeCases:
    """Roundtrip tests for edge cases and special scenarios."""

    def test_empty_lists_roundtrip(self):
        """Test handling of empty lists."""
        json_text = '{"items": []}'
        original_data = json.loads(json_text)

        loop_candidates = analyze_loops("json", original_data)
        flat_items = flatten_config("json", original_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", original_data, "test", None, loop_candidates
        )

        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = json.loads(regenerated_text)

        assert regenerated_data == original_data

    def test_special_characters_roundtrip(self):
        """Test handling of special characters."""
        json_data = {
            "quote": 'He said "hello"',
            "backslash": "path\\to\\file",
            "newline": "line1\nline2",
            "unicode": "emoji: 🚀",
        }

        loop_candidates = analyze_loops("json", json_data)
        flat_items = flatten_config("json", json_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", json_data, "test", None, loop_candidates
        )

        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = json.loads(regenerated_text)

        assert regenerated_data == json_data

    def test_numeric_types_roundtrip(self):
        """Test preservation of numeric types."""
        json_data = {
            "int": 42,
            "float": 3.14159,
            "negative": -100,
            "zero": 0,
            "large": 9999999999,
        }

        loop_candidates = analyze_loops("json", json_data)
        flat_items = flatten_config("json", json_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", json_data, "test", None, loop_candidates
        )

        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = json.loads(regenerated_text)

        assert regenerated_data == json_data

    def test_boolean_preservation_roundtrip(self):
        """Test that booleans are preserved correctly."""
        yaml_text = """
        enabled: true
        disabled: false
        """

        original_data = yaml.safe_load(yaml_text)

        loop_candidates = analyze_loops("yaml", original_data)
        flat_items = flatten_config("yaml", original_data, loop_candidates)

        ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "yaml", original_data, "test", yaml_text, loop_candidates
        )

        variables = yaml.safe_load(ansible_yaml)
        regenerated_text = render_template(template, variables)
        regenerated_data = yaml.safe_load(regenerated_text)

        # Both should be actual booleans
        assert regenerated_data["enabled"] is True
        assert regenerated_data["disabled"] is False


# Helper functions


def _compare_toml_data(data1: Any, data2: Any) -> bool:
    """Compare TOML data, handling datetime objects."""
    import datetime

    if type(data1) != type(data2):
        return False

    if isinstance(data1, dict):
        if set(data1.keys()) != set(data2.keys()):
            return False
        return all(_compare_toml_data(data1[k], data2[k]) for k in data1.keys())

    elif isinstance(data1, list):
        if len(data1) != len(data2):
            return False
        return all(_compare_toml_data(v1, v2) for v1, v2 in zip(data1, data2))

    elif isinstance(data1, datetime.datetime):
        # Compare datetime objects
        return data1 == data2

    else:
        return data1 == data2


def _xml_elements_equal(elem1, elem2, ignore_whitespace: bool = False) -> bool:
    """Compare two XML elements for equality."""
    # Compare tags
    if elem1.tag != elem2.tag:
        return False

    # Compare attributes
    if elem1.attrib != elem2.attrib:
        return False

    # Compare text
    text1 = (elem1.text or "").strip() if ignore_whitespace else (elem1.text or "")
    text2 = (elem2.text or "").strip() if ignore_whitespace else (elem2.text or "")
    if text1 != text2:
        return False

    # Compare tail
    tail1 = (elem1.tail or "").strip() if ignore_whitespace else (elem1.tail or "")
    tail2 = (elem2.tail or "").strip() if ignore_whitespace else (elem2.tail or "")
    if tail1 != tail2:
        return False

    # Compare children
    children1 = list(elem1)
    children2 = list(elem2)

    if len(children1) != len(children2):
        return False

    return all(
        _xml_elements_equal(c1, c2, ignore_whitespace)
        for c1, c2 in zip(children1, children2)
    )


def _ini_configs_equal(config1, config2) -> bool:
    """Compare two ConfigParser objects for equality."""
    if set(config1.sections()) != set(config2.sections()):
        return False

    for section in config1.sections():
        if set(config1.options(section)) != set(config2.options(section)):
            return False

        for option in config1.options(section):
            if config1.get(section, option) != config2.get(section, option):
                return False

    return True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
