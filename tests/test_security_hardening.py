from __future__ import annotations

from pathlib import Path
import os

import pytest
import yaml
from defusedxml.common import EntitiesForbidden

from jinjaturtle import cli
from jinjaturtle.core import generate_ansible_yaml, parse_config, flatten_config
from jinjaturtle.multi import is_supported_file, iter_supported_files, process_directory
from jinjaturtle.output_safety import OutputPathError, write_text_safely


class UnsafeAwareLoader(yaml.SafeLoader):
    pass


def _unsafe(loader: UnsafeAwareLoader, node: yaml.Node):
    return loader.construct_scalar(node)


UnsafeAwareLoader.add_constructor("!unsafe", _unsafe)


def test_jinja_values_are_emitted_as_ansible_unsafe(tmp_path: Path):
    src = tmp_path / "app.ini"
    src.write_text("[main]\ncmd = {{ lookup('pipe','id') }}\n", encoding="utf-8")

    fmt, parsed = parse_config(src)
    defaults_yaml = generate_ansible_yaml("role", flatten_config(fmt, parsed))

    assert "role_main_cmd: !unsafe" in defaults_yaml
    assert "{{ lookup(''pipe'',''id'') }}" in defaults_yaml
    loaded = yaml.load(defaults_yaml, Loader=UnsafeAwareLoader)
    assert loaded["role_main_cmd"] == "{{ lookup('pipe','id') }}"


def test_folder_mode_marks_nested_jinja_values_and_ids_unsafe(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    # Filename ids are source-derived values too.
    (src / "{{ bad }}.yaml").write_text(
        "message: \"{{ lookup('pipe','id') }}\"\n", encoding="utf-8"
    )

    defaults_yaml, _outputs = process_directory(
        src, recursive=False, role_prefix="role"
    )

    assert "id: !unsafe" in defaults_yaml
    assert "role_message: !unsafe" in defaults_yaml
    loaded = yaml.load(defaults_yaml, Loader=UnsafeAwareLoader)
    assert loaded["role_items"][0]["id"] == "{{ bad }}.yaml"
    assert loaded["role_items"][0]["role_message"] == "{{ lookup('pipe','id') }}"


def test_xml_parser_rejects_entities_when_called_as_library(tmp_path: Path):
    src = tmp_path / "bad.xml"
    src.write_text(
        "<!DOCTYPE root [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><root>&xxe;</root>",
        encoding="utf-8",
    )

    with pytest.raises(EntitiesForbidden):
        parse_config(src, "xml")


def test_folder_mode_does_not_follow_symlinked_files(tmp_path: Path):
    real = tmp_path / "secret.ini"
    real.write_text("[main]\nsecret=yes\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link.ini"
    link.symlink_to(real)

    assert not is_supported_file(link)
    assert iter_supported_files(root, recursive=False) == []
    assert iter_supported_files(root, recursive=True) == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_cli_refuses_to_write_through_final_symlink(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "out.yml"
    link.symlink_to(target)

    with pytest.raises(OutputPathError):
        write_text_safely(link, "replace\n")

    assert target.read_text(encoding="utf-8") == "keep\n"
    assert link.is_symlink()


def test_cli_refuses_symlinked_output_parent(tmp_path: Path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "linkdir"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(OutputPathError):
        write_text_safely(link_dir / "out.yml", "data\n")

    assert not (real_dir / "out.yml").exists()


def test_cli_reports_unsafe_output_path_without_overwriting_symlink(tmp_path: Path):
    cfg = tmp_path / "app.ini"
    cfg.write_text("[main]\nname = ok\n", encoding="utf-8")
    target = tmp_path / "target.yml"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "defaults.yml"
    link.symlink_to(target)

    exit_code = cli._main([str(cfg), "--defaults-output", str(link)])

    assert exit_code == 2
    assert target.read_text(encoding="utf-8") == "keep\n"
