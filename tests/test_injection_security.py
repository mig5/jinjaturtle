"""Security regression tests for template injection (SSTI).

JinjaTurtle copies parts of the source config (comments, unrecognised lines,
structural keys) verbatim into the generated template.  If that text contains
Jinja2/ERB delimiters it must be neutralised, otherwise attacker-influenced
config content becomes live template code that executes when Salt/Ansible/Puppet
later renders the template.

These tests render the *generated* template the way a downstream tool would and
assert that an injected payload never executes.  A tripwire object is exposed
under every name a payload might reference; if the rendered output ever contains
the tripwire sentinel, an injected expression executed and the test fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import jinja2
import pytest
import yaml as pyyaml

from jinjaturtle.escape import escape_jinja_literal, escape_erb_literal


TRIP = "__TRIPWIRE_FIRED__"


class _Boom:
    """Returns the tripwire sentinel for any access/call an SSTI payload makes."""

    def run(self, *a, **k):
        return TRIP

    def __call__(self, *a, **k):
        return TRIP

    def __getitem__(self, k):
        return self

    def __getattr__(self, n):
        return _Boom()

    def __str__(self):
        return TRIP


def _render_jinja(template_text: str, defaults: dict) -> str:
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.filters.setdefault("to_json", lambda v, **k: __import__("json").dumps(v))
    env.filters.setdefault("lower", lambda v: str(v).lower())
    ctx = dict(defaults or {})
    for name in ("salt", "cmd", "os", "subprocess", "cycler", "lipsum", "namespace"):
        ctx.setdefault(name, _Boom())
    return env.from_string(template_text).render(**ctx)


def _run_jinjaturtle(tmp_path: Path, source_name: str, body: str, fmt: str):
    src = tmp_path / source_name
    src.write_text(body, encoding="utf-8")
    tpl = tmp_path / "out.tpl"
    dfl = tmp_path / "defaults.yml"
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "jinjaturtle.cli",
            str(src),
            "-f",
            fmt,
            "--role-name",
            "role",
            "-t",
            str(tpl),
            "-d",
            str(dfl),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"generation failed: {res.stderr}"
    defaults = pyyaml.safe_load(dfl.read_text()) or {}
    return tpl.read_text(), defaults


# A representative payload for each format, placed where the format allows
# attacker-controlled verbatim text (comments / unrecognised lines).
FORMAT_CASES = [
    (
        "ini",
        "evil.ini",
        "[s]\n"
        "good = ok ; {{ salt['cmd.run']('id') }}\n"
        "# {{ cmd.run('whoami') }}\n",
    ),
    (
        "yaml",
        "evil.yaml",
        "server:\n" "  motd: ok\n" "  # {{ salt['cmd.run']('id') }}\n",
    ),
    (
        "toml",
        "evil.toml",
        "[s]\n" 'good = "ok"\n' "# {{ cmd.run('id') }}\n",
    ),
    (
        "xml",
        "evil.xml",
        "<config>\n"
        "  <!-- {{ salt['cmd.run']('id') }} -->\n"
        '  <server name="ok"><motd>ok</motd></server>\n'
        "</config>\n",
    ),
    (
        "postfix",
        "main.cf",
        "myhostname = mail.example.com\n" "# {{ salt['cmd.run']('id') }}\n",
    ),
    (
        "systemd",
        "evil.service",
        "[Unit]\n"
        "Description=ok\n"
        "# {{ cmd.run('id') }}\n"
        "RawLineNoEquals {% for x in ().__class__.__bases__ %}\n"
        "[Service]\n"
        "ExecStart=/bin/true\n",
    ),
    (
        "ssh",
        "sshd_config",
        "# {{ salt['cmd.run']('id') }}\n" "Port 22\n" "PermitRootLogin no\n",
    ),
]


@pytest.mark.parametrize("fmt,name,body", FORMAT_CASES)
def test_comment_payload_does_not_execute(tmp_path, fmt, name, body):
    template_text, defaults = _run_jinjaturtle(tmp_path, name, body, fmt)
    rendered = _render_jinja(template_text, defaults)
    assert TRIP not in rendered, f"injected payload executed for {fmt}:\n{rendered}"


@pytest.mark.parametrize("fmt,name,body", FORMAT_CASES)
def test_generated_template_is_renderable(tmp_path, fmt, name, body):
    # A correct escape must still produce a syntactically valid template.
    template_text, defaults = _run_jinjaturtle(tmp_path, name, body, fmt)
    # Should not raise a TemplateSyntaxError.
    _render_jinja(template_text, defaults)


# --- Unit-level guarantees for the escaper itself ---------------------------

SSTI_PAYLOADS = [
    "{{ 7*7 }}",
    "{{ salt['cmd.run']('id') }}",
    "{% set x = cycler.__init__.__globals__ %}{{ x }}",
    "{# comment payload #}",
    "text {% endraw %} breakout {{ evil }}",
    "nested {%  endraw  %} spacing {{ evil }}",
    "{%- endraw -%}{{ evil }}",
    "mixed {{ a }} and {% b %} and {# c #}",
    "}}{{ orphan delimiters %}{%",
]


@pytest.mark.parametrize("payload", SSTI_PAYLOADS)
def test_escape_jinja_literal_renders_back_to_original(payload):
    """Escaped text must render to the exact original characters, inertly."""
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    escaped = escape_jinja_literal(payload)
    rendered = env.from_string(escaped).render(evil="EVIL", x="X", a="A")
    assert rendered == payload
    assert TRIP not in rendered


def test_escape_jinja_literal_noop_on_plain_text():
    for plain in ["", "hello world", "# a normal comment", "port = 8080", "key: value"]:
        assert escape_jinja_literal(plain) == plain


def test_escape_jinja_literal_actually_blocks_execution():
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    payload = "{{ boom.run('x') }}"
    escaped = escape_jinja_literal(payload)
    rendered = env.from_string(escaped).render(boom=_Boom())
    assert TRIP not in rendered
    # Sanity: the *unescaped* payload would have fired the tripwire.
    fired = env.from_string(payload).render(boom=_Boom())
    assert TRIP in fired


@pytest.mark.parametrize(
    "payload",
    [
        "<%= system('id') %>",
        "<% require 'open3' %>",
        "text <%= 1+1 %> more",
        "-%> orphan <%",
    ],
)
def test_escape_erb_literal_removes_executable_tags(payload):
    escaped = escape_erb_literal(payload)
    # No raw executable ERB tag should survive that contains the original code.
    live = re.findall(r"<%[-=#]?(.*?)-?%>", escaped, re.S)
    for chunk in live:
        assert "system" not in chunk
        assert "require" not in chunk
        # Numeric/expression payloads must be reduced to literal-string prints.
