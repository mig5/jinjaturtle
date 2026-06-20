from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jinjaturtle.multi import (
    _collect_dict_like_paths,
    _merge_union,
    defined_var_name,
    is_supported_file,
    iter_supported_files,
    process_directory,
)


def test_iter_supported_files_handles_files_dirs_and_missing_paths(tmp_path: Path):
    json_file = tmp_path / "config.json"
    text_file = tmp_path / "notes.txt"
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_yaml = nested / "config.yaml"

    json_file.write_text('{"name": "one"}\n', encoding="utf-8")
    text_file.write_text("ignore me\n", encoding="utf-8")
    nested_yaml.write_text("name: two\n", encoding="utf-8")

    assert is_supported_file(json_file)
    assert not is_supported_file(text_file)
    assert iter_supported_files(json_file, recursive=False) == [json_file]
    assert iter_supported_files(text_file, recursive=False) == []
    assert iter_supported_files(tmp_path, recursive=False) == [json_file]
    assert iter_supported_files(tmp_path, recursive=True) == [json_file, nested_yaml]

    with pytest.raises(FileNotFoundError):
        iter_supported_files(tmp_path / "missing", recursive=False)


def test_merge_union_and_collect_dict_like_paths():
    merged = _merge_union(
        {"name": "one", "ports": [80], "nested": {"a": 1}},
        {"ports": [80, 443], "nested": {"b": 2}, "enabled": True},
    )

    assert merged == {
        "name": "one",
        "ports": [80, 443],
        "nested": {"a": 1, "b": 2},
        "enabled": True,
    }

    containers, leaves = _collect_dict_like_paths(merged)
    assert ("nested",) in containers
    assert ("nested", "a") in containers
    assert ("ports", "1") in leaves


def test_process_directory_multiple_formats(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"name": "one"}\n', encoding="utf-8")
    (tmp_path / "b.yaml").write_text("name: one\nextra: true\n", encoding="utf-8")
    (tmp_path / "c.toml").write_text('name = "one"\n', encoding="utf-8")
    (tmp_path / "d.ini").write_text("[main]\nname = one\n", encoding="utf-8")
    (tmp_path / "e.xml").write_text("<root><name>one</name></root>", encoding="utf-8")

    defaults_yaml, outputs = process_directory(
        tmp_path, recursive=False, role_prefix="role"
    )
    defaults = yaml.safe_load(defaults_yaml)
    by_fmt = {output.fmt: output for output in outputs}

    assert set(by_fmt) == {"ini", "json", "toml", "xml", "yaml"}
    assert set(defaults) == {
        "role_ini_items",
        "role_json_items",
        "role_toml_items",
        "role_xml_items",
        "role_yaml_items",
    }
    assert defaults["role_json_items"][0]["data"] == {"name": "one"}
    assert (
        by_fmt["json"].template
        == "{{ data | to_json(indent=2, ensure_ascii=False) }}\n"
    )
    assert "{{ role_main_name }}" in by_fmt["ini"].template
    assert "{{ role_name }}" in by_fmt["xml"].template


def test_process_directory_yaml_union_marks_optional_keys(tmp_path: Path):
    (tmp_path / "one.yaml").write_text("name: one\nextra: yes\n", encoding="utf-8")
    (tmp_path / "two.yaml").write_text("name: two\n", encoding="utf-8")

    defaults_yaml, outputs = process_directory(
        tmp_path, recursive=False, role_prefix="role"
    )
    defaults = yaml.safe_load(defaults_yaml)

    assert len(outputs) == 1
    assert outputs[0].fmt == "yaml"
    assert "{% if role_defined_extra is defined %}" in outputs[0].template
    assert defaults["role_items"][0][defined_var_name("role", ("extra",))] is True
    assert defined_var_name("role", ("extra",)) not in defaults["role_items"][1]


def test_process_directory_ini_union_marks_optional_sections_and_keys(tmp_path: Path):
    (tmp_path / "one.ini").write_text(
        "[main]\nname = one\n[extra]\nflag = yes\n", encoding="utf-8"
    )
    (tmp_path / "two.ini").write_text("[main]\nname = two\n", encoding="utf-8")

    defaults_yaml, outputs = process_directory(
        tmp_path, recursive=False, role_prefix="role"
    )
    defaults = yaml.safe_load(defaults_yaml)

    assert len(outputs) == 1
    assert outputs[0].fmt == "ini"
    assert "{% if role_defined_extra is defined %}" in outputs[0].template
    assert defaults["role_items"][0][defined_var_name("role", ("extra",))] is True
    assert defined_var_name("role", ("extra",)) not in defaults["role_items"][1]


def test_process_directory_rejects_empty_folder(tmp_path: Path):
    with pytest.raises(ValueError, match="No supported config files"):
        process_directory(tmp_path, recursive=False, role_prefix="role")
