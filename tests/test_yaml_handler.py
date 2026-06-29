from __future__ import annotations

from pathlib import Path
import textwrap

import yaml

from jinjaturtle.core import (
    parse_config,
    analyze_loops,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)
from jinjaturtle.handlers.yaml import YamlHandler

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_yaml_roundtrip_with_list_and_comment():
    yaml_path = SAMPLES_DIR / "bar.yaml"
    assert yaml_path.is_file(), f"Missing sample YAML file: {yaml_path}"

    fmt, parsed = parse_config(yaml_path)
    assert fmt == "yaml"

    flat_items = flatten_config(fmt, parsed)
    ansible_yaml = generate_ansible_yaml("foobar", flat_items)
    defaults = yaml.safe_load(ansible_yaml)

    # Defaults: keys are flattened with indices
    assert defaults["foobar_foo"] == "bar"
    assert defaults["foobar_blah_0"] == "something"
    assert defaults["foobar_blah_1"] == "else"

    # Template generation (preserving comments)
    original_text = yaml_path.read_text(encoding="utf-8")
    template = generate_jinja2_template(
        fmt, parsed, "foobar", original_text=original_text
    )

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

    handler = YamlHandler()
    tmpl = handler._generate_yaml_template_from_text("role", text)

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


def test_generate_jinja2_template_yaml_structural_fallback():
    """
    When original_text is not provided for YAML, generate_jinja2_template should use
    the structural fallback path (yaml.safe_dump + handler processing).
    """
    parsed = {"outer": {"inner": "val"}}

    tmpl = generate_jinja2_template("yaml", parsed=parsed, role_prefix="role")

    # We don't care about exact formatting, just that the expected variable
    # name shows up, proving we went through the structural path.
    assert "role_outer_inner" in tmpl


def test_yaml_empty_collection_defaults_match_template_vars(tmp_path: Path):
    yaml_path = tmp_path / "pdk.yaml"
    yaml_path.write_text("ignore: []\nsettings: {}\n", encoding="utf-8")

    fmt, parsed = parse_config(yaml_path)
    flat_items = flatten_config(fmt, parsed)
    ansible_yaml = generate_ansible_yaml("role", flat_items)
    defaults = yaml.safe_load(ansible_yaml)

    assert defaults["role_ignore"] == []
    assert defaults["role_settings"] == {}

    template = generate_jinja2_template(
        fmt, parsed, "role", original_text=yaml_path.read_text(encoding="utf-8")
    )
    assert "{{ role_ignore }}" in template
    assert "{{ role_settings }}" in template


def test_yaml_indentless_sequence_loop_roundtrips_semantically(tmp_path: Path):
    """Indentless YAML sequences under a mapping key must be fully replaced.

    A previous loop renderer emitted a loop at ``images:`` but then processed
    the original ``- item`` lines again because they had the same indentation
    as the parent key. That duplicated values and nested following top-level
    keys incorrectly.
    """
    from jinja2 import Template

    from jinjaturtle.core import analyze_loops

    text = textwrap.dedent(
        """
        default:
          provisioner: docker
          images:
          - waffleimage/ubuntu18.04
          - waffleimage/centos7
        vagrant:
          provisioner: vagrant
          images:
          - centos/7
          - generic/ubuntu1804
        """
    ).lstrip()
    path = tmp_path / "provision.yaml"
    path.write_text(text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)
    defaults = yaml.safe_load(
        generate_ansible_yaml("role", flat_items, loop_candidates)
    )
    template = generate_jinja2_template(
        fmt, parsed, "role", original_text=text, loop_candidates=loop_candidates
    )
    rendered = Template(template).render(**defaults)

    assert yaml.safe_load(rendered) == yaml.safe_load(text)
    assert "%}  - {{ image }}" in template
    assert "%}    - {{ image }}" not in template
    assert "%}      - {{ image }}" not in template
    assert "  vagrant:" not in template


def test_yaml_loop_preserves_blank_separator_after_list(tmp_path: Path):
    from jinja2 import Template

    cases = [
        (
            "blank",
            "require:\n  - rubocop-performance\n  - rubocop-rspec\n\nAllCops:\n  NewCops: enable\n",
            "\n  - rubocop-rspec\n\nAllCops:",
        ),
        (
            "no_blank",
            "require:\n  - rubocop-performance\n  - rubocop-rspec\nAllCops:\n  NewCops: enable\n",
            "\n  - rubocop-rspec\nAllCops:",
        ),
    ]

    for label, text, rendered_expected in cases:
        path = tmp_path / f"{label}.yml"
        path.write_text(text, encoding="utf-8")

        fmt, parsed = parse_config(path)
        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)
        defaults = yaml.safe_load(
            generate_ansible_yaml("role", flat_items, loop_candidates)
        )

        template = generate_jinja2_template(
            fmt, parsed, "role", original_text=text, loop_candidates=loop_candidates
        )
        rendered = Template(template).render(**defaults)
        assert rendered_expected in rendered


def test_yaml_loop_preserves_following_top_level_comments(tmp_path: Path):
    from jinja2 import Environment, Template

    from jinjaturtle.core import analyze_loops

    text = textwrap.dedent(
        """
        Style/HashExcept:
          Exclude:
            - lib/puppet/provider/dsc_base_provider/dsc_base_provider.rb
            - spec/unit/puppet/provider/dsc_base_provider/dsc_base_provider_spec.rb

        # Offense count: 2
        # This cop supports unsafe autocorrection (--autocorrect-all).
        Style/MapIntoArray:
          Exclude:
            - lib/puppet/provider/dsc_base_provider/dsc_base_provider.rb
        """
    ).lstrip()
    path = tmp_path / ".rubocop_todo.yml"
    path.write_text(text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)
    defaults = yaml.safe_load(
        generate_ansible_yaml("role", flat_items, loop_candidates)
    )
    template = generate_jinja2_template(
        fmt, parsed, "role", original_text=text, loop_candidates=loop_candidates
    )
    rendered = Template(template).render(**defaults)
    ansible_rendered = (
        Environment(trim_blocks=True).from_string(template).render(**defaults)
    )

    assert "# Offense count: 2" in rendered
    assert "# This cop supports unsafe autocorrection" in rendered
    assert "spec.rb\n\n# Offense count: 2" in ansible_rendered
    assert yaml.safe_load(rendered) == yaml.safe_load(text)
    assert yaml.safe_load(ansible_rendered) == yaml.safe_load(text)


def test_yaml_scalar_loop_preserves_quoted_list_items(tmp_path: Path):
    from jinja2 import Template

    text = textwrap.dedent(
        """
        files:
          - 'spec/unit/puppet/provider/dsc_base_provider/dsc_base_provider_spec.rb'
          - 'spec/unit/pwsh/util_spec.rb'
          - 'spec/unit/pwsh/windows_powershell_spec.rb'
        """
    ).lstrip()
    path = tmp_path / "quoted-list.yaml"
    path.write_text(text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)
    defaults = yaml.safe_load(
        generate_ansible_yaml("role", flat_items, loop_candidates)
    )
    template = generate_jinja2_template(
        fmt, parsed, "role", original_text=text, loop_candidates=loop_candidates
    )
    rendered = Template(template).render(**defaults)

    assert loop_candidates
    assert "- '{{" in template
    assert rendered == text


def test_yaml_bool_scalars_render_with_yaml_spelling(tmp_path: Path):
    from jinja2 import Template

    text = textwrap.dedent(
        """
        AllCops:
          SuggestExtensions: false
        Style/ClassAndModuleChildren:
          Enabled: false
        """
    ).lstrip()
    path = tmp_path / ".rubocop.yml"
    path.write_text(text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    flat_items = flatten_config(fmt, parsed)
    defaults = yaml.safe_load(generate_ansible_yaml("role", flat_items))
    template = generate_jinja2_template(fmt, parsed, "role", original_text=text)
    rendered = Template(template).render(**defaults)

    assert "SuggestExtensions: false" in rendered
    assert "Enabled: false" in rendered
    assert "SuggestExtensions: False" not in rendered
    assert "Enabled: False" not in rendered
    assert yaml.safe_load(rendered) == yaml.safe_load(text)
