"""
Tests to ensure all Jinja2 template variables exist in the Ansible YAML.

These tests catch the bug where templates reference variables that don't exist
because the YAML has a list but the template uses scalar references (or vice versa).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set
import yaml
import pytest

from jinjaturtle.core import (
    parse_config,
    analyze_loops,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)


def extract_jinja_variables(template: str) -> Set[str]:
    """
    Extract all Jinja2 variable names from a template that must exist in YAML.

    Extracts variables from:
    - {{ variable_name }}
    - {{ variable.field }}
    - {% for item in collection %}

    Returns only the base variable names that must be defined in YAML.
    Filters out loop variables (the 'item' part of 'for item in collection').
    """
    variables = set()

    # First, find all loop variables (these are defined by the template, not YAML)
    loop_vars = set()
    for_pattern = r"\{%\s*for\s+(\w+)\s+in\s+([a-zA-Z_][a-zA-Z0-9_]*)"
    for match in re.finditer(for_pattern, template):
        loop_var = match.group(1)  # The item
        collection = match.group(2)  # The collection
        loop_vars.add(loop_var)
        variables.add(collection)  # Collection must exist in YAML

    # Pattern 1: {{ variable_name }} or {{ variable.field }}
    # Captures the first part before any dots or filters
    var_pattern = r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)"
    for match in re.finditer(var_pattern, template):
        var_name = match.group(1)
        # Only add if it's not a loop variable
        if var_name not in loop_vars:
            variables.add(var_name)

    return variables


def extract_yaml_variables(ansible_yaml: str) -> Set[str]:
    """
    Extract all variable names from Ansible YAML.

    Returns the top-level keys from the YAML document.
    """
    data = yaml.safe_load(ansible_yaml)
    if not isinstance(data, dict):
        return set()
    return set(data.keys())


class TestTemplateYamlConsistency:
    """Tests that verify template variables exist in YAML."""

    def test_simple_json_consistency(self):
        """Simple JSON with scalars and lists."""
        json_text = """
        {
          "name": "test",
          "values": [1, 2, 3]
        }
        """

        fmt = "json"
        import json

        parsed = json.loads(json_text)

        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(fmt, parsed, "app", None, loop_candidates)

        yaml_vars = extract_yaml_variables(ansible_yaml)
        template_vars = extract_jinja_variables(template)

        # Every variable in template must exist in YAML
        missing_vars = template_vars - yaml_vars
        assert not missing_vars, (
            f"Template references variables not in YAML: {missing_vars}\n"
            f"YAML vars: {yaml_vars}\n"
            f"Template vars: {template_vars}\n"
            f"Template:\n{template}\n"
            f"YAML:\n{ansible_yaml}"
        )

    def test_toml_inline_array_consistency(self):
        """TOML with inline array should use loops consistently."""
        import tomllib

        toml_text = """
        name = "myapp"
        servers = ["server1", "server2", "server3"]
        """

        parsed = tomllib.loads(toml_text)
        loop_candidates = analyze_loops("toml", parsed)
        flat_items = flatten_config("toml", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "toml", parsed, "app", toml_text, loop_candidates
        )

        yaml_vars = extract_yaml_variables(ansible_yaml)
        template_vars = extract_jinja_variables(template)

        missing_vars = template_vars - yaml_vars
        assert not missing_vars, (
            f"Template references variables not in YAML: {missing_vars}\n"
            f"Template:\n{template}\n"
            f"YAML:\n{ansible_yaml}"
        )

    def test_toml_array_of_tables_consistency(self):
        """TOML with [[array.of.tables]] should use loops consistently."""
        import tomllib

        toml_text = """
        [[database]]
        host = "db1.example.com"
        port = 5432

        [[database]]
        host = "db2.example.com"
        port = 5433
        """

        parsed = tomllib.loads(toml_text)
        loop_candidates = analyze_loops("toml", parsed)
        flat_items = flatten_config("toml", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "toml", parsed, "app", toml_text, loop_candidates
        )

        yaml_vars = extract_yaml_variables(ansible_yaml)
        template_vars = extract_jinja_variables(template)

        missing_vars = template_vars - yaml_vars
        assert not missing_vars, (
            f"Template references variables not in YAML: {missing_vars}\n"
            f"Template:\n{template}\n"
            f"YAML:\n{ansible_yaml}"
        )

        # Additionally verify that if YAML has a list, template uses a loop
        defaults = yaml.safe_load(ansible_yaml)
        for var_name, value in defaults.items():
            if isinstance(value, list) and len(value) > 1:
                # YAML has a list - template should use {% for %}
                assert "{% for" in template, (
                    f"YAML has list variable '{var_name}' but template doesn't use loops\n"
                    f"Template:\n{template}"
                )

    def test_yaml_list_consistency(self):
        """YAML with lists should use loops consistently."""
        yaml_text = """
        name: myapp
        servers:
          - server1
          - server2
          - server3
        databases:
          - host: db1
            port: 5432
          - host: db2
            port: 5433
        """

        parsed = yaml.safe_load(yaml_text)
        loop_candidates = analyze_loops("yaml", parsed)
        flat_items = flatten_config("yaml", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "yaml", parsed, "app", yaml_text, loop_candidates
        )

        yaml_vars = extract_yaml_variables(ansible_yaml)
        template_vars = extract_jinja_variables(template)

        missing_vars = template_vars - yaml_vars
        assert not missing_vars, (
            f"Template references variables not in YAML: {missing_vars}\n"
            f"Template:\n{template}\n"
            f"YAML:\n{ansible_yaml}"
        )

    def test_mixed_scalars_and_loops_consistency(self):
        """Config with both scalars and loops should be consistent."""
        import tomllib

        toml_text = """
        name = "myapp"
        version = "1.0"
        ports = [8080, 8081, 8082]

        [database]
        host = "localhost"
        port = 5432

        [[servers]]
        name = "web1"
        ip = "10.0.0.1"

        [[servers]]
        name = "web2"
        ip = "10.0.0.2"
        """

        parsed = tomllib.loads(toml_text)
        loop_candidates = analyze_loops("toml", parsed)
        flat_items = flatten_config("toml", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "toml", parsed, "app", toml_text, loop_candidates
        )

        yaml_vars = extract_yaml_variables(ansible_yaml)
        template_vars = extract_jinja_variables(template)

        missing_vars = template_vars - yaml_vars
        assert not missing_vars, (
            f"Template references variables not in YAML: {missing_vars}\n"
            f"Template:\n{template}\n"
            f"YAML:\n{ansible_yaml}"
        )

    def test_no_orphaned_scalar_references(self):
        """
        When YAML has a list variable, template must NOT reference scalar indices.

        This catches the bug where:
        - YAML has: app_list: [1, 2, 3]
        - Template incorrectly uses: {{ app_list_0 }}, {{ app_list_1 }}
        """
        import json

        json_text = '{"items": [1, 2, 3, 4, 5]}'
        parsed = json.loads(json_text)

        loop_candidates = analyze_loops("json", parsed)
        flat_items = flatten_config("json", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", parsed, "app", None, loop_candidates
        )

        defaults = yaml.safe_load(ansible_yaml)

        # Check each list variable in YAML
        for var_name, value in defaults.items():
            if isinstance(value, list):
                # Template should NOT reference app_items_0, app_items_1, etc.
                for i in range(len(value)):
                    scalar_ref = f"{var_name}_{i}"
                    assert scalar_ref not in template, (
                        f"Template incorrectly uses scalar reference '{scalar_ref}' "
                        f"when YAML has '{var_name}' as a list\n"
                        f"Template should use loops, not scalar indices\n"
                        f"Template:\n{template}"
                    )

    def test_all_sample_files_consistency(self):
        """Test all sample files for consistency."""
        samples_dir = Path(__file__).parent / "samples"

        sample_files = [
            ("foo.json", "json"),
            ("bar.yaml", "yaml"),
            ("tom.toml", "toml"),
        ]

        for filename, fmt in sample_files:
            file_path = samples_dir / filename
            if not file_path.exists():
                pytest.skip(f"Sample file {filename} not found")

            original_text = file_path.read_text()
            fmt_detected, parsed = parse_config(file_path)

            loop_candidates = analyze_loops(fmt_detected, parsed)
            flat_items = flatten_config(fmt_detected, parsed, loop_candidates)

            ansible_yaml = generate_ansible_yaml("test", flat_items, loop_candidates)
            template = generate_jinja2_template(
                fmt_detected, parsed, "test", original_text, loop_candidates
            )

            yaml_vars = extract_yaml_variables(ansible_yaml)
            template_vars = extract_jinja_variables(template)

            missing_vars = template_vars - yaml_vars
            assert not missing_vars, (
                f"File: {filename}\n"
                f"Template references variables not in YAML: {missing_vars}\n"
                f"YAML vars: {yaml_vars}\n"
                f"Template vars: {template_vars}\n"
                f"Template:\n{template}\n"
                f"YAML:\n{ansible_yaml}"
            )


class TestStructuralConsistency:
    """Tests that verify structural consistency between YAML and templates."""

    def test_list_in_yaml_means_loop_in_template(self):
        """When YAML has a list (len > 1), template should use {% for %}."""
        import json

        json_text = """
        {
          "scalar": "value",
          "list": [1, 2, 3]
        }
        """

        parsed = json.loads(json_text)
        loop_candidates = analyze_loops("json", parsed)
        flat_items = flatten_config("json", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", parsed, "app", None, loop_candidates
        )

        defaults = yaml.safe_load(ansible_yaml)

        # Find list variables in YAML
        list_vars = [
            k for k, v in defaults.items() if isinstance(v, list) and len(v) > 1
        ]

        if list_vars:
            # Template must contain for loops
            assert "{% for" in template, (
                f"YAML has list variables {list_vars} but template has no loops\n"
                f"Template:\n{template}"
            )

            # Each list variable should be used in a for loop
            for var_name in list_vars:
                # Look for "{% for ... in var_name %}"
                for_pattern = (
                    r"\{%\s*for\s+\w+\s+in\s+" + re.escape(var_name) + r"\s*%\}"
                )
                assert re.search(for_pattern, template), (
                    f"List variable '{var_name}' not used in a for loop\n"
                    f"Template:\n{template}"
                )

    def test_scalar_in_yaml_means_no_loop_in_template(self):
        """When YAML has scalars, template should use {{ var }}, not loops."""
        import json

        json_text = """
        {
          "name": "test",
          "port": 8080,
          "enabled": true
        }
        """

        parsed = json.loads(json_text)
        loop_candidates = analyze_loops("json", parsed)
        flat_items = flatten_config("json", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", parsed, "app", None, loop_candidates
        )

        defaults = yaml.safe_load(ansible_yaml)

        # All variables are scalars - template should NOT have loops
        scalar_vars = [
            k for k, v in defaults.items() if not isinstance(v, (list, dict))
        ]

        # Check that scalar vars are used directly, not in loops
        for var_name in scalar_vars:
            # Should appear in {{ var_name }}, not {% for ... in var_name %}
            direct_ref = f"{{{{ {var_name}"
            loop_ref = f"for .* in {var_name}"

            assert direct_ref in template, (
                f"Scalar variable '{var_name}' should be directly referenced\n"
                f"Template:\n{template}"
            )

            assert not re.search(loop_ref, template), (
                f"Scalar variable '{var_name}' incorrectly used in a loop\n"
                f"Template:\n{template}"
            )

    def test_no_undefined_variable_errors(self):
        """
        Simulate Ansible template rendering to catch undefined variables.

        This is the ultimate test - actually render the template with the YAML
        and verify no undefined variable errors occur.
        """
        from jinja2 import Environment, StrictUndefined
        import json

        json_text = """
        {
          "name": "myapp",
          "servers": ["web1", "web2"],
          "database": {
            "host": "localhost",
            "port": 5432
          }
        }
        """

        parsed = json.loads(json_text)
        loop_candidates = analyze_loops("json", parsed)
        flat_items = flatten_config("json", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", parsed, "app", None, loop_candidates
        )

        # Load variables from YAML
        variables = yaml.safe_load(ansible_yaml)

        # Try to render the template
        env = Environment(undefined=StrictUndefined)
        try:
            jinja_template = env.from_string(template)
            rendered = jinja_template.render(variables)

            # Successfully rendered - this is what we want!
            assert rendered, "Template rendered successfully"

        except Exception as e:
            pytest.fail(
                f"Template rendering failed with variables from YAML\n"
                f"Error: {e}\n"
                f"Template:\n{template}\n"
                f"Variables:\n{ansible_yaml}"
            )


class TestRegressionBugs:
    """Tests for specific bugs that were found and fixed."""

    def test_toml_array_of_tables_no_scalar_refs(self):
        """
        Regression test: TOML [[array]] should not generate scalar references.

        Bug: Template had {{ app_database_host }} when YAML had app_database as list.
        """
        import tomllib

        toml_text = """
        [[database]]
        host = "db1"
        port = 5432

        [[database]]
        host = "db2"
        port = 5433
        """

        parsed = tomllib.loads(toml_text)
        loop_candidates = analyze_loops("toml", parsed)
        flat_items = flatten_config("toml", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "toml", parsed, "app", toml_text, loop_candidates
        )

        # YAML should have app_database as a list
        defaults = yaml.safe_load(ansible_yaml)
        assert isinstance(
            defaults.get("app_database"), list
        ), f"Expected app_database to be a list in YAML\n{ansible_yaml}"

        # Template should NOT have app_database_host or app_database_port
        assert (
            "app_database_host" not in template
        ), f"Template incorrectly uses scalar 'app_database_host'\n{template}"
        assert (
            "app_database_port" not in template
        ), f"Template incorrectly uses scalar 'app_database_port'\n{template}"

        # Template SHOULD use a loop
        assert "{% for" in template, f"Template should use a loop\n{template}"
        assert (
            "app_database" in template
        ), f"Template should reference app_database\n{template}"

    def test_json_array_no_index_refs(self):
        """
        Regression test: JSON arrays should not generate index references.

        Bug: Template had {{ app_list_0 }}, {{ app_list_1 }} when YAML had app_list as list.
        """
        import json

        json_text = '{"items": [1, 2, 3]}'
        parsed = json.loads(json_text)

        loop_candidates = analyze_loops("json", parsed)
        flat_items = flatten_config("json", parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            "json", parsed, "app", None, loop_candidates
        )

        # YAML should have app_items as a list
        defaults = yaml.safe_load(ansible_yaml)
        assert isinstance(defaults.get("app_items"), list)

        # Template should NOT have app_items_0, app_items_1, app_items_2
        for i in range(3):
            assert (
                f"app_items_{i}" not in template
            ), f"Template incorrectly uses scalar 'app_items_{i}'\n{template}"

        # Template SHOULD use a loop
        assert "{% for" in template
        assert "app_items" in template


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
