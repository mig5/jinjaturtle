"""Regression tests for the output safety gate (``jinjaturtle.safety``).

The gate is JinjaTurtle's second, independent line of defence against template
injection.  Where the per-handler escaper neutralises verbatim source text, the
gate inspects the *finished* template and refuses to emit it if any live
construct is not one JinjaTurtle itself produces.  These tests cover:

  * the gate's allow/deny grammar (unit level);
  * the two concrete injection findings that motivated it -- JSON object keys
    and folder-mode union keys copied into templates unescaped;
  * end-to-end CLI fail-closed behaviour (non-zero exit, no file written).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jinjaturtle import core
from jinjaturtle.multi import process_directory
from jinjaturtle.safety import (
    TemplateSafetyError,
    verify_jinja2_template_safe,
    verify_erb_template_safe,
)


# --------------------------------------------------------------------------- #
# Unit: the allow/deny grammar.
# --------------------------------------------------------------------------- #

LEGIT_TEMPLATES = [
    "{{ demo_memory_limit }}",
    "{{ demo_x | to_json(ensure_ascii=False) }}",
    "{{ demo_a | to_json(indent=2, ensure_ascii=False) }}",
    "{{ demo_name | lower }}",
    "{{ 'true' if demo_flag else 'false' }}",
    '{{ "true" if demo_flag else "false" }}',
    "{{ 'null' if demo_v is none else demo_v }}",
    "{% for server in demo_servers %}{{ server.name }}{% endfor %}",
    "{{ server.config.port }}",
    "{% if demo_x is defined %}{{ demo_x }}{% endif %}",
    "{% if demo_x is none %}x{% endif %}",
    "{% if not loop.last %},{% endif %}",
    "plain text with no tags",
    "{% raw %}# literal {{ not_code }} {% if x %}{% endraw %}",
    "{% raw %}{{ 7*7 }}{% endraw %}: value",  # escaped key (folder-mode fix)
]


@pytest.mark.parametrize("template", LEGIT_TEMPLATES)
def test_gate_allows_jinjaturtle_constructs(template):
    # Must not raise.
    verify_jinja2_template_safe(template)


MALICIOUS_TEMPLATES = [
    "{{ 7*7 }}",
    "{{ cycler.__init__.__globals__ }}",
    "{{ cycler.__init__.__globals__.os.popen('id').read() }}",
    "{{ salt['cmd.run']('id') }}",
    "{{ self.__init__ }}",
    "{% for x in ().__class__.__base__.__subclasses__() %}{{ x }}{% endfor %}",
    "{{ config.items() }}",
    '{{ request["application"] }}',
    "{% set x = 1 %}",
    "{{ lipsum.__globals__ }}",
    "{{ a.__class__.__mro__ }}",
    "{{ ''.join(['a','b']) }}",
    "{% include 'x' %}",
    "{% import 'x' as y %}",
]


@pytest.mark.parametrize("template", MALICIOUS_TEMPLATES)
def test_gate_blocks_injection(template):
    with pytest.raises(TemplateSafetyError):
        verify_jinja2_template_safe(template)


def test_gate_blocks_double_underscore_anywhere():
    # The no-dunder rule is the backbone of blocking attribute-traversal SSTI.
    with pytest.raises(TemplateSafetyError):
        verify_jinja2_template_safe("{{ demo__x }}")
    with pytest.raises(TemplateSafetyError):
        verify_jinja2_template_safe("{{ a.b__c }}")


def test_gate_rejects_unlexable_breakout_as_safety_error():
    # A raw-wrapper breakout that leaves dangling tags must surface as a
    # TemplateSafetyError, not a raw Jinja2 syntax error.
    broken = "{% raw %}{%+ endraw %}{{ 7*7 }}"  # unbalanced on purpose
    with pytest.raises(TemplateSafetyError):
        verify_jinja2_template_safe(broken)


# --------------------------------------------------------------------------- #
# Finding 1: JSON object keys copied verbatim into the template.
# --------------------------------------------------------------------------- #

JSON_KEY_PAYLOADS = [
    '{ "{{ 7*7 }}": "v" }',
    '{ "{{cycler.__init__.__globals__}}": 1 }',
    "{ \"ok\": { \"{{ salt['cmd.run']('id') }}\": 2 } }",
]


@pytest.mark.parametrize("src", JSON_KEY_PAYLOADS)
def test_json_key_injection_is_blocked(src):
    parsed = json.loads(src)
    with pytest.raises(TemplateSafetyError):
        core.generate_jinja2_template("json", parsed, "demo", original_text=src)


def test_json_benign_template_still_generates():
    src = '{ "name": "app", "port": 8080 }'
    parsed = json.loads(src)
    out = core.generate_jinja2_template("json", parsed, "demo", original_text=src)
    assert "demo_name" in out
    assert "demo_port" in out


# --------------------------------------------------------------------------- #
# Finding 2: folder-mode union renderers copied keys verbatim.
# --------------------------------------------------------------------------- #


def _write(tmp: Path, name: str, text: str) -> None:
    (tmp / name).write_text(text, encoding="utf-8")


def test_folder_yaml_key_injection_is_neutralised(tmp_path):
    # The malicious key must be escaped (root-cause fix), so the union template
    # generates successfully AND passes the gate, rendering the key inertly.
    _write(tmp_path, "a.yaml", '"{{ 7*7 }}": value\nnormalkey: ok\n')
    _write(tmp_path, "b.yaml", "normalkey: ok2\n")
    _defaults, outputs = process_directory(tmp_path, False, "demo")
    template = "\n".join(o.template for o in outputs)
    # Escaped, not live:
    assert "{% raw %}{{ 7*7 }}{% endraw %}" in template
    # And the gate (run inside process_directory) did not reject it.


def test_folder_ini_section_and_key_injection_neutralised(tmp_path):
    _write(
        tmp_path,
        "a.ini",
        "[{{ 7*7 }}]\n{{ evil }} = x\n",
    )
    _write(tmp_path, "b.ini", "[normal]\nk = y\n")
    _defaults, outputs = process_directory(tmp_path, False, "demo")
    template = "\n".join(o.template for o in outputs)
    # Section header and key are escaped, not live.
    assert "{{ 7*7 }}" not in _strip_raw_blocks(template)
    assert "{{ evil }}" not in _strip_raw_blocks(template)


def test_folder_toml_key_injection_neutralised(tmp_path):
    _write(tmp_path, "a.toml", '"{{ 7*7 }}" = "v"\nok = "y"\n')
    _write(tmp_path, "b.toml", 'ok = "z"\n')
    _defaults, outputs = process_directory(tmp_path, False, "demo")
    template = "\n".join(o.template for o in outputs)
    assert "{{ 7*7 }}" not in _strip_raw_blocks(template)


def _strip_raw_blocks(text: str) -> str:
    """Remove the *contents* of raw blocks so we can assert no LIVE payload
    remains outside them."""
    import re

    return re.sub(
        r"{%[-+]?\s*raw\s*[-+]?%}.*?{%[-+]?\s*endraw\s*[-+]?%}",
        "",
        text,
        flags=re.S,
    )


# --------------------------------------------------------------------------- #
# ERB gate.
# --------------------------------------------------------------------------- #


def test_erb_gate_blocks_leftover_jinja_delimiters():
    with pytest.raises(TemplateSafetyError):
        verify_erb_template_safe("ok <%= @x %> but {{ leftover }} here")


def test_erb_gate_allows_clean_erb():
    verify_erb_template_safe("memory = <%= @memory_limit %>\n")


# --------------------------------------------------------------------------- #
# End-to-end CLI: fail closed.
# --------------------------------------------------------------------------- #


def _run_cli(args, env_extra=None):
    import os

    env = {**os.environ, "PYTHONPATH": "src"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "jinjaturtle.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_cli_fails_closed_on_injection(tmp_path):
    bad = tmp_path / "evil.json"
    bad.write_text('{ "{{ 7*7 }}": "v" }', encoding="utf-8")
    out = tmp_path / "out.j2"
    result = _run_cli([str(bad), "-r", "demo", "-f", "json", "-t", str(out)])
    assert result.returncode == 2
    assert "refusing to generate unsafe template" in result.stderr
    # No template file may be written when the gate refuses.
    assert not out.exists()


def test_cli_succeeds_on_benign_input(tmp_path):
    good = tmp_path / "ok.json"
    good.write_text('{ "name": "app" }', encoding="utf-8")
    out = tmp_path / "out.j2"
    result = _run_cli([str(good), "-r", "demo", "-f", "json", "-t", str(out)])
    assert result.returncode == 0
    assert out.exists()
