"""Regression tests for malformed-input handling (``ConfigParseError``).

Bug: every parse-layer handler (xml/json/toml/yaml/ini) used to let its
underlying parser's exception escape as an unhandled traceback when given a
malformed file. Pointing JinjaTurtle (or Enroll, which calls it as a library)
at harvested, attacker-influenceable config makes malformed input an entirely
expected condition, so it must fail cleanly instead of crashing.

These tests pin down that:

  * ``parse_config`` raises the normalised ``ConfigParseError`` for malformed
    XML/JSON/TOML/INI;
  * defusedxml's *security* exceptions (XXE / ``EntitiesForbidden``) are NOT
    swallowed by that normalisation -- they must still propagate so a caller can
    distinguish "malformed" from "malicious";
  * an unrelated error (e.g. the TOML "tomllib missing" ``RuntimeError``) is not
    captured by the normalisation either;
  * the CLI exits non-zero with a clean message (no traceback) on malformed
    input; and
  * folder mode skips an unparseable file instead of aborting the whole run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jinjaturtle.core import ConfigParseError, parse_config


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "jinjaturtle.cli", *args],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "fmt,filename,content",
    [
        # Element name is not well-formed XML -> expat ParseError historically
        # escaped as an uncaught traceback.
        ("xml", "bad.xml", "<root><{{tag}}/></root>"),
        ("xml", "bad2.xml", "<root><unclosed></root>"),
        ("json", "bad.json", "{not valid json"),
        ("toml", "bad.toml", "x = = ="),
        # A bare key with no value is invalid INI for configparser.
        ("ini", "bad.ini", "[s]\nthis line has no equals and no colon\n"),
    ],
)
def test_parse_config_raises_configparseerror_on_malformed(
    tmp_path: Path, fmt: str, filename: str, content: str
) -> None:
    src = tmp_path / filename
    src.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigParseError):
        parse_config(src, fmt=fmt)


def test_parse_config_does_not_swallow_xxe_security_exception(tmp_path: Path) -> None:
    """defusedxml's EntitiesForbidden must propagate, not become ConfigParseError.

    EntitiesForbidden subclasses ValueError, so a naive ``except ValueError`` /
    ``except Exception`` in parse_config would mask an XXE attempt as a benign
    "malformed file". The normalisation is deliberately scoped to exclude it.
    """
    from defusedxml.common import EntitiesForbidden

    xxe = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
        "<foo>&xxe;</foo>\n"
    )
    src = tmp_path / "xxe.xml"
    src.write_text(xxe, encoding="utf-8")
    with pytest.raises(EntitiesForbidden):
        parse_config(src, fmt="xml")


def test_parse_config_does_not_swallow_unrelated_runtimeerror(
    tmp_path: Path, monkeypatch
) -> None:
    """A non-malformation error (tomllib missing) must propagate unchanged."""
    import jinjaturtle.handlers.toml as toml_module

    monkeypatch.setattr(toml_module, "tomllib", None)
    src = tmp_path / "x.toml"
    src.write_text('a = "b"\n', encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        parse_config(src, fmt="toml")
    assert "tomllib/tomli is required" in str(exc.value)


def test_cli_fails_cleanly_on_malformed_xml(tmp_path: Path) -> None:
    """The CLI must exit non-zero with a clean message, not a traceback."""
    src = tmp_path / "bad.xml"
    src.write_text("<root><{{tag}}/></root>", encoding="utf-8")
    res = _run_cli([str(src), "-f", "xml", "-r", "demo"])
    assert res.returncode != 0
    # Clean, user-facing message -- not a Python traceback.
    assert "could not parse" in res.stderr
    assert "Traceback (most recent call last)" not in res.stderr


@pytest.mark.parametrize(
    "content",
    [
        # Classic XXE: external SYSTEM entity used to read a local file.
        (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>\n'
            "<root>&xxe;</root>\n"
        ),
        # Billion-laughs style internal entity expansion.
        (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>\n'
            "<lolz>&lol2;</lolz>\n"
        ),
        # External parameter entity (SSRF / out-of-band XXE vector).
        (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE root [<!ENTITY % ext SYSTEM "http://127.0.0.1:9/x.dtd"> %ext;]>\n'
            "<root>x</root>\n"
        ),
    ],
)
def test_cli_refuses_xxe_cleanly(tmp_path: Path, content: str) -> None:
    """defusedxml's security refusal must surface as a clean non-zero exit.

    ``EntitiesForbidden``/``DTDForbidden``/``ExternalReferenceForbidden`` subclass
    ``DefusedXmlException`` (and ``ValueError``). ``parse_config`` deliberately
    lets them propagate rather than folding them into ``ConfigParseError`` so a
    blocked attack stays distinguishable from a benign malformed file. The CLI
    must therefore catch ``DefusedXmlException`` itself and exit cleanly instead
    of leaking a Python traceback.
    """
    src = tmp_path / "xxe.xml"
    src.write_text(content, encoding="utf-8")
    res = _run_cli([str(src), "-f", "xml", "-r", "demo"])
    assert res.returncode == 2
    assert "refusing unsafe XML input" in res.stderr
    assert "Traceback (most recent call last)" not in res.stderr


def test_folder_mode_skips_unparseable_file(tmp_path: Path) -> None:
    """One malformed file should not abort processing of the whole directory."""
    from jinjaturtle.multi import process_directory

    good = tmp_path / "good.json"
    good.write_text('{ "host": "localhost" }', encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")

    defaults_yaml, outputs = process_directory(tmp_path, False, "demo")
    # The good file still produced output despite the bad sibling: its content
    # and source id appear, and a template was generated.
    assert "localhost" in defaults_yaml
    assert "good.json" in defaults_yaml
    assert outputs


@pytest.mark.parametrize(
    "content",
    [
        "a: &a [*a]\n",  # self-referential sequence alias
        "root: &r\n  child: *r\n",  # self-referential mapping alias
    ],
)
def test_recursive_yaml_is_rejected_cleanly(tmp_path: Path, content: str) -> None:
    """A recursive YAML anchor must fail as a clean ConfigParseError.

    PyYAML's safe_load builds a self-referential object from a recursive anchor;
    JinjaTurtle's downstream walks (flatten, timestamp-stringify, template gen)
    would otherwise blow the stack with a RecursionError traceback. Harvested
    config is attacker-influenceable, so this must fail closed.
    """
    src = tmp_path / "cyclic.yaml"
    src.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigParseError):
        parse_config(src, "yaml")


def test_recursive_yaml_cli_exits_nonzero_without_traceback(tmp_path: Path) -> None:
    src = tmp_path / "cyclic.yaml"
    src.write_text("a: &a [*a]\n", encoding="utf-8")
    res = _run_cli([str(src), "-f", "yaml", "-r", "role"])
    assert res.returncode != 0
    assert "Traceback" not in res.stderr
    assert "RecursionError" not in res.stderr


def test_shared_noncyclic_yaml_aliases_still_work(tmp_path: Path) -> None:
    """A shared (acyclic) anchor referenced multiple times is a DAG, not a
    cycle, and must still parse and template normally."""
    src = tmp_path / "shared.yaml"
    src.write_text("base: &b\n  x: 1\na: *b\nc: *b\n", encoding="utf-8")
    fmt, parsed = parse_config(src, "yaml")
    assert fmt == "yaml"
    assert parsed["a"] == {"x": 1}
    assert parsed["c"] == {"x": 1}
