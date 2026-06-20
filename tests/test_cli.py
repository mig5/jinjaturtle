from __future__ import annotations

from pathlib import Path
from jinjaturtle import cli

SAMPLES_DIR = Path(__file__).parent / "samples"


def test_cli_stdout_toml(capsys):
    """
    Run the CLI on the TOML sample, printing to stdout.
    Covers the no-output-path branches.
    """
    cfg_path = SAMPLES_DIR / "tom.toml"
    exit_code = cli._main([str(cfg_path), "-r", "jinjaturtle"])

    assert exit_code == 0

    captured = capsys.readouterr()
    out = captured.out

    # Headers printed when not writing to files
    assert "# defaults/main.yml" in out
    assert "# config.j2" in out
    # Should contain some variables
    assert "jinjaturtle_" in out
    assert captured.err == ""


def test_cli_writes_output_files(tmp_path, capsys):
    """
    Run the CLI on the INI sample, writing to files instead of stdout.
    Covers the --defaults-output and --template-output branches.
    """
    cfg_path = SAMPLES_DIR / "php.ini"
    defaults_path = tmp_path / "defaults.yml"
    template_path = tmp_path / "config.j2"

    exit_code = cli._main(
        [
            str(cfg_path),
            "-r",
            "php",
            "--defaults-output",
            str(defaults_path),
            "--template-output",
            str(template_path),
        ]
    )

    assert exit_code == 0
    assert defaults_path.is_file()
    assert template_path.is_file()

    defaults_text = defaults_path.read_text(encoding="utf-8")
    template_text = template_path.read_text(encoding="utf-8")

    assert "php_" in defaults_text
    assert "php_" in template_text

    captured = capsys.readouterr()
    # When writing to files, we shouldn't print the big headers
    assert "# defaults/main.yml" not in captured.out
    assert "# config.j2" not in captured.out


def test_cli_folder_stdout(tmp_path, capsys):
    """Folder mode without output paths prints defaults and all templates."""
    (tmp_path / "a.json").write_text('{"name": "one"}\n', encoding="utf-8")
    (tmp_path / "b.yaml").write_text("name: two\n", encoding="utf-8")

    exit_code = cli._main([str(tmp_path), "-r", "role"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "# defaults/main.yml" in captured.out
    assert "# config.json.j2" in captured.out
    assert "# config.yaml.j2" in captured.out
    assert "role_json_items" in captured.out
    assert "role_yaml_items" in captured.out


def test_cli_folder_writes_template_directory(tmp_path, capsys):
    """Folder mode writes multiple templates when template output is a directory."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.json").write_text('{"name": "one"}\n', encoding="utf-8")
    (src_dir / "b.yaml").write_text("name: two\n", encoding="utf-8")
    defaults_path = tmp_path / "defaults.yml"
    template_dir = tmp_path / "templates"

    exit_code = cli._main(
        [
            str(src_dir),
            "-r",
            "role",
            "--defaults-output",
            str(defaults_path),
            "--template-output",
            str(template_dir),
        ]
    )

    assert exit_code == 0
    assert defaults_path.is_file()
    assert (template_dir / "config.json.j2").is_file()
    assert (template_dir / "config.yaml.j2").is_file()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cli_folder_single_output_file_when_one_format(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.json").write_text('{"name": "one"}\n', encoding="utf-8")
    defaults_path = tmp_path / "defaults.yml"
    template_path = tmp_path / "config.j2"

    exit_code = cli._main(
        [
            str(src_dir),
            "-r",
            "role",
            "--defaults-output",
            str(defaults_path),
            "--template-output",
            str(template_path),
        ]
    )

    assert exit_code == 0
    assert defaults_path.is_file()
    assert template_path.is_file()
    assert "to_json" in template_path.read_text(encoding="utf-8")


def test_cli_erb_outputs_puppet_hiera_and_erb_template(tmp_path):
    cfg = tmp_path / "app.ini"
    cfg.write_text("[main]\nport = 8080\n", encoding="utf-8")
    data_out = tmp_path / "common.yaml"
    template_out = tmp_path / "app.ini.erb"

    exit_code = cli._main(
        [
            str(cfg),
            "-r",
            "php",
            "--template-engine",
            "erb",
            "--defaults-output",
            str(data_out),
            "--template-output",
            str(template_out),
        ]
    )

    assert exit_code == 0
    assert "php::main_port: '8080'" in data_out.read_text(encoding="utf-8")
    assert "port = <%= @main_port %>" in template_out.read_text(encoding="utf-8")


def test_cli_erb_can_use_separate_puppet_class_namespace(tmp_path):
    cfg = tmp_path / "app.ini"
    cfg.write_text("[main]\nport = 8080\n", encoding="utf-8")
    data_out = tmp_path / "node.yaml"
    template_out = tmp_path / "app.ini.erb"

    exit_code = cli._main(
        [
            str(cfg),
            "-r",
            "php_etc_app_ini",
            "--template-engine",
            "erb",
            "--puppet-class",
            "php",
            "--defaults-output",
            str(data_out),
            "--template-output",
            str(template_out),
        ]
    )

    assert exit_code == 0
    data = data_out.read_text(encoding="utf-8")
    template = template_out.read_text(encoding="utf-8")
    assert "php::php_etc_app_ini_main_port: '8080'" in data
    assert "port = <%= @php_etc_app_ini_main_port %>" in template
