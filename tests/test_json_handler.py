from __future__ import annotations

from pathlib import Path
import json

import pytest
import yaml
from jinja2 import Environment

from jinjaturtle.core import (
    parse_config,
    flatten_config,
    generate_ansible_yaml,
    analyze_loops,
    generate_jinja2_template,
)
from jinjaturtle.handlers.json import JsonHandler
from jinjaturtle.loop_analyzer import LoopCandidate

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_json_roundtrip():
    json_path = SAMPLES_DIR / "foo.json"
    assert json_path.is_file(), f"Missing sample JSON file: {json_path}"

    fmt, parsed = parse_config(json_path)
    assert fmt == "json"

    # With loop detection
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)
    ansible_yaml = generate_ansible_yaml("foobar", flat_items, loop_candidates)
    defaults = yaml.safe_load(ansible_yaml)

    # Defaults: nested keys
    assert defaults["foobar_foo"] == "bar"
    assert defaults["foobar_nested_a"] == 1
    # Booleans are now preserved as booleans (not stringified)
    assert defaults["foobar_nested_b"] is True
    # JSON stays scalar/index-based so source formatting can be preserved.
    assert loop_candidates == []
    assert defaults["foobar_list_0"] == 10
    assert defaults["foobar_list_1"] == 20

    template = generate_jinja2_template("json", parsed, "foobar", None, loop_candidates)

    # Template should use Ansible | to_json for type preservation without
    # HTML-safe escaping.
    assert "{{ foobar_foo | to_json(ensure_ascii=False) }}" in template
    assert "{{ foobar_nested_a | to_json(ensure_ascii=False) }}" in template
    assert "{{ foobar_nested_b | to_json(ensure_ascii=False) }}" in template
    assert "{{ foobar_list_0 | to_json(ensure_ascii=False) }}" in template
    assert "{{ foobar_list_1 | to_json(ensure_ascii=False) }}" in template
    assert "{% for" not in template


def test_json_template_preserves_original_indentation_and_final_newline_state(
    tmp_path: Path,
):
    json_text = (
        "{\n"
        '    "default-runtime": "runsc",\n'
        '    "runtimes": {\n'
        '        "runsc": {\n'
        '            "path": "/usr/bin/runsc"\n'
        "        }\n"
        "    }\n"
        "}"
    )
    path = tmp_path / "daemon.json"
    path.write_text(json_text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    template = generate_jinja2_template(fmt, parsed, "docker", json_text, [])

    assert '    "default-runtime"' in template
    assert '        "runsc"' in template
    assert '            "path"' in template
    assert not template.endswith("\n")


def test_json_template_uses_non_html_safe_json_filter_for_angle_brackets(
    tmp_path: Path,
):
    json_text = '{"version_requirement": ">= 8.0.0 < 9.0.0"}\n'
    path = tmp_path / "metadata.json"
    path.write_text(json_text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    template = generate_jinja2_template(fmt, parsed, "puppet", json_text, [])

    assert "to_json(ensure_ascii=False)" in template
    assert "tojson" not in template


def test_json_inline_object_array_roundtrips_without_expanding(tmp_path: Path):
    json_text = (
        "{\n"
        '  "description": "Gets the last boot time of a Linux or Windows system",\n'
        '  "implementations": [\n'
        '    {"name": "last_boot_time_nix.sh", "requirements": ["shell"]},\n'
        '    {"name": "last_boot_time_win.ps1", "requirements": ["powershell"]}\n'
        "  ]\n"
        "}\n"
    )
    path = tmp_path / "metadata.json"
    path.write_text(json_text, encoding="utf-8")

    fmt, parsed = parse_config(path)
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)
    defaults = yaml.safe_load(
        generate_ansible_yaml("fact", flat_items, loop_candidates)
    )
    template = generate_jinja2_template(fmt, parsed, "fact", json_text, loop_candidates)

    env = Environment(keep_trailing_newline=True)
    env.filters["to_json"] = lambda value, **kwargs: json.dumps(value, **kwargs)
    rendered = env.from_string(template).render(**defaults)

    assert loop_candidates == []
    assert '{"name": {{ fact_implementations_0_name' in template
    assert rendered == json_text


def test_json_direct_loop_generation_renders_valid_json_without_joined_objects():
    items = [
        {"name": "last_boot_time_nix.sh", "requirements": ["shell"]},
        {"name": "last_boot_time_win.ps1", "requirements": ["powershell"]},
    ]
    parsed = {"implementations": items}
    candidate = LoopCandidate(
        path=("implementations",),
        loop_var="implementation",
        items=items,
        item_schema="simple_dict",
    )
    handler = JsonHandler()
    template = handler.generate_jinja2_template_with_loops(
        parsed, "fact", None, [candidate]
    )

    env = Environment(keep_trailing_newline=True)
    env.filters["to_json"] = lambda value, **kwargs: json.dumps(value, **kwargs)
    rendered = env.from_string(template).render(fact_implementations=items)

    assert "},    {" not in rendered
    assert json.loads(rendered) == parsed


def test_json_scalar_span_collector_empty_containers_and_invalid_json():
    handler = JsonHandler()

    assert handler._collect_json_scalar_spans('{"empty": [], "obj": {}}') == []
    assert handler._collect_json_scalar_spans('{"unterminated": [1,}') is None


def test_json_template_falls_back_when_source_scanner_cannot_collect_spans(monkeypatch):
    handler = JsonHandler()
    monkeypatch.setattr(handler, "_collect_json_scalar_spans", lambda _text: None)

    template = handler.generate_jinja2_template(
        {"answer": 42}, "role", original_text='{"answer": 42}'
    )

    assert (
        template
        == '{\n  "answer": {{ role_answer | to_json(ensure_ascii=False) }}\n}\n'
    )


def test_generate_jinja2_template_json_type_error():
    """
    Wrong type for JSON in JsonHandler.generate_jinja2_template should raise TypeError.
    """
    handler = JsonHandler()
    with pytest.raises(TypeError):
        handler.generate_jinja2_template(parsed="not a dict", role_prefix="role")
