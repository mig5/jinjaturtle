"""Security regression tests for template injection (SSTI).

JinjaTurtle copies parts of the source config (comments, unrecognised lines,
structural keys) verbatim into the generated template.  If that text contains
Jinja2/ERB delimiters it must be neutralised, otherwise attacker-influenced
config content becomes live template code that executes when Ansible later
renders the template.

These tests render the *generated* template the way a downstream tool would and
assert that an injected payload never executes.  A tripwire object is exposed
under every name a payload might reference; if the rendered output ever contains
the tripwire sentinel, an injected expression executed and the test fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import jinja2
import pytest
import yaml as pyyaml

from jinjaturtle.escape import (
    escape_jinja_literal,
)

TRIP = "__TRIPWIRE_FIRED__"


class _UnsafeAwareLoader(pyyaml.SafeLoader):
    pass


def _construct_unsafe(loader: _UnsafeAwareLoader, node: pyyaml.Node):
    return loader.construct_scalar(node)


_UnsafeAwareLoader.add_constructor("!unsafe", _construct_unsafe)


def _safe_load_defaults(text: str):
    return pyyaml.load(text, Loader=_UnsafeAwareLoader)


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
    defaults = _safe_load_defaults(dfl.read_text()) or {}
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


# --- JSON object-key injection ------------------------------------------------
#
# The JSON handler copies the text *between* scalar values (object keys,
# punctuation) verbatim. A key is never a value placeholder, so Jinja markup in a
# key can only come from attacker-influenced source text. The output gate
# (verify_no_live_jinja_in_json_keys) must fail closed on it -- including the
# "benign-looking name" form (e.g. ``{{ ansible_hostname }}``) that the generic
# allowlist would otherwise accept as an ordinary variable reference, and which
# could leak an in-scope variable's value into the rendered config at apply time.

JSON_KEY_INJECTION_BODIES = [
    # benign-looking variable reference (the residual bypass: passes the generic
    # allowlist but must still be rejected in *key* position)
    '{ "{{ ansible_hostname }}": "v" }',
    # dotted reference (e.g. dumping another host's vars)
    '{ "{{ hostvars.localhost }}": 1 }',
    # self-referencing a sibling-derived variable name
    '{ "{{ role_port }}": "x", "port": 8080 }',
    # classic gadget (already rejected historically; kept as a guard)
    '{ "{{ cycler.__init__.__globals__ }}": 1 }',
    # statement injection in a key
    '{ "{% for x in y %}k{% endfor %}": 1 }',
    # nested object key
    '{ "ok": { "{{ ansible_hostname }}": 2 } }',
]


@pytest.mark.parametrize("body", JSON_KEY_INJECTION_BODIES)
def test_json_key_injection_fails_closed_cli(tmp_path, body):
    src = tmp_path / "evil.json"
    src.write_text(body, encoding="utf-8")
    out = tmp_path / "out.j2"
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "jinjaturtle.cli",
            str(src),
            "-f",
            "json",
            "--role-name",
            "role",
            "-t",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2, f"expected fail-closed, got rc={res.returncode}"
    assert "refusing to generate unsafe template" in res.stderr
    assert not out.exists(), "no template may be written when the gate refuses"


def test_json_benign_keys_still_generate(tmp_path):
    src = tmp_path / "ok.json"
    src.write_text('{ "host": "localhost", "port": 8080 }', encoding="utf-8")
    out = tmp_path / "out.j2"
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "jinjaturtle.cli",
            str(src),
            "-f",
            "json",
            "--role-name",
            "demo",
            "-t",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    template_text = out.read_text()
    # Keys stay literal; values become placeholders.
    assert '"host":' in template_text
    assert "demo_host" in template_text


# --- Unit-level guarantees for the escaper itself ---------------------------

SSTI_PAYLOADS = [
    "{{ 7*7 }}",
    "{{ salt['cmd.run']('id') }}",
    "{% set x = cycler.__init__.__globals__ %}{{ x }}",
    "{# comment payload #}",
    "text {% endraw %} breakout {{ evil }}",
    "nested {%  endraw  %} spacing {{ evil }}",
    "{%- endraw -%}{{ evil }}",
    # Whitespace-control markers: Jinja2 accepts "-", "+" or none adjacent to a
    # tag's delimiters, and every variant closes a {% raw %} block.  The "+"
    # forms in particular were a raw-wrapper breakout vector (the defang regex
    # historically only matched "-"), so all combinations must be neutralised.
    "{%+ endraw %}{{ evil }}",
    "{% endraw +%}{{ evil }}",
    "{%+ endraw +%}{{ evil }}",
    "{%- endraw +%}{{ evil }}",
    "{%+ endraw -%}{{ evil }}",
    # Full breakout attempt: close raw early, inject live code, re-open raw to
    # swallow our trailing {% endraw %} so the template would otherwise compile.
    "{%+ endraw %}{{ evil }}{%+ raw %}",
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
    "endraw",
    [
        "{% endraw %}",
        "{% endraw -%}",
        "{% endraw +%}",
        "{%- endraw %}",
        "{%- endraw -%}",
        "{%- endraw +%}",
        "{%+ endraw %}",
        "{%+ endraw -%}",
        "{%+ endraw +%}",
    ],
)
def test_endraw_whitespace_control_cannot_break_out(endraw):
    """Every whitespace-control form of endraw closes a {% raw %} block in
    Jinja2, so each must be defanged.  A payload that closes raw early, injects
    a live tripwire call, then re-opens raw to balance the wrapper must still
    render inertly back to its original characters."""
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    payload = f"{endraw}{{{{ boom.run('x') }}}}{{%+ raw %}}"
    escaped = escape_jinja_literal(payload)
    rendered = env.from_string(escaped).render(boom=_Boom())
    assert TRIP not in rendered
    assert rendered == payload
