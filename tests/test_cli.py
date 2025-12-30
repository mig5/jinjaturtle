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
