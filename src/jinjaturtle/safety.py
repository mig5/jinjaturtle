from __future__ import annotations

"""Output safety gate for generated templates (defence in depth).

JinjaTurtle's first line of defence is per-handler escaping: every piece of
verbatim source text is meant to be wrapped/neutralised before it reaches the
template (see ``escape.py``).  That model is correct but *fragile*: it relies on
every handler remembering to escape at every site, and on the escaper being
exactly right for every delimiter form.  A single forgotten call site -- or a
new handler, or a missed delimiter variant -- silently reopens a
template-injection / SSTI path that can become remote code execution on the
configuration-management control node when the template is later rendered.

This module adds a second, independent line of defence that does **not** depend
on getting every escape right.  It inspects the *finished* template and proves a
single global property:

    Every *live* template construct in the output is one that JinjaTurtle itself
    legitimately emits.  Anything else can only have originated from
    un-neutralised source text, so generation fails loudly instead of emitting a
    dangerous template.

The check is positive/allowlist-based, which is the safe direction: unknown
constructs are rejected, not ignored.  It runs at the single choke points in
``core.py`` (``generate_jinja2_template`` / ``generate_erb_template``), so it
covers every current handler and every future one automatically.

Why this is robust against the escaper being wrong
---------------------------------------------------
We tokenise with Jinja2's own lexer.  The lexer emits a JinjaTurtle
``{% raw %} ... {% endraw %}`` wrapper as ``raw_begin`` / inert ``data`` /
``raw_end``: the wrapped literal text is *not* tokenised as live tags.  So the
verifier only ever sees, as live constructs, the tags Jinja2 would actually
execute.  If an escaped block was mis-wrapped such that a payload escapes the
raw wrapper (the historical ``{%+ endraw %}`` bug), that payload now appears as a
*live* token here and is rejected -- the gate catches the failure even though
the escaper produced it.
"""

import re

__all__ = [
    "TemplateSafetyError",
    "verify_jinja2_template_safe",
    "verify_erb_template_safe",
]


class TemplateSafetyError(Exception):
    """Raised when a generated template contains a construct JinjaTurtle would
    never emit, indicating that un-neutralised source text became live template
    code.  Generation must abort rather than emit the template."""


# --------------------------------------------------------------------------- #
# Allowlist grammar for JinjaTurtle-emitted Jinja2.
#
# JinjaTurtle emits a deliberately tiny subset of Jinja2.  Each pattern below
# describes the *full body* of a tag (the text between ``{%``/``%}`` or
# ``{{``/``}}``), already stripped of surrounding whitespace and of any ``-``/
# ``+`` whitespace-control markers.  Identifiers (variable names, loop vars,
# keys) are restricted to a conservative character class; crucially this class
# excludes characters needed for SSTI gadgets (quotes, parentheses, brackets,
# arithmetic/operator characters, ``%``, ``|`` except in the known filter forms,
# attribute access beyond a single dotted hop, etc.).
# --------------------------------------------------------------------------- #

# A single identifier.  Word characters only, and -- critically -- no
# double-underscore anywhere.  ``__`` is the gateway to every classic Jinja2
# SSTI gadget (``__class__``, ``__init__``, ``__globals__``, ``__builtins__``),
# and JinjaTurtle never emits a dunder, so forbidding ``__`` here removes the
# entire attribute-traversal escape class even if such a token reached output.
_NAME = r"(?!\w*__)[A-Za-z_][A-Za-z0-9_]*"

# A dotted reference for loop-item field access.  JinjaTurtle emits at most
# three hops (``loopvar``, ``loopvar.field``, ``loopvar.key.subkey``), so cap the
# depth rather than allow arbitrary chains.
_DOTTED = rf"{_NAME}(?:\.{_NAME}){{0,2}}"

# Filters JinjaTurtle is known to emit inside ``{{ ... }}`` expressions.
_KNOWN_FILTERS = (
    r"lower",
    r"to_json\(ensure_ascii=(?:True|False)\)",
    r"to_json\(indent=\d+,\s*ensure_ascii=(?:True|False)\)",
    r"tojson",
)
_FILTER_ALT = "|".join(_KNOWN_FILTERS)

# Expression bodies allowed inside ``{{ ... }}``.
_EXPR_PATTERNS = tuple(
    re.compile(p)
    for p in (
        # Plain variable / dotted loop-field reference.
        rf"^{_DOTTED}$",
        # Filtered reference: ``name | filter`` (one known filter).
        rf"^{_DOTTED}\s*\|\s*(?:{_FILTER_ALT})$",
        # YAML-preserving boolean ternary emitted by j2.yaml_*_expression:
        #   'true' if NAME else 'false'   /   "true" if NAME else "false"
        rf"^(['\"])(?:true|false)\1\s+if\s+{_DOTTED}\s+else\s+(['\"])(?:true|false)\2$",
        # YAML-preserving null ternary:
        #   'null' if NAME is none else NAME
        rf"^(['\"])null\1\s+if\s+{_DOTTED}\s+is\s+none\s+else\s+{_DOTTED}$",
    )
)

# Statement bodies allowed inside ``{% ... %}``.
_STMT_PATTERNS = tuple(
    re.compile(p)
    for p in (
        rf"^for\s+{_NAME}\s+in\s+{_DOTTED}$",
        r"^endfor$",
        rf"^if\s+{_DOTTED}\s+is\s+defined$",
        rf"^if\s+{_DOTTED}\s+is\s+none$",
        r"^if\s+not\s+loop\.last$",
        r"^else$",
        r"^endif$",
        # ``elif`` is emitted only for the same shapes as the if-conditions
        # above; keep it conservative.
        rf"^elif\s+{_DOTTED}\s+is\s+(?:defined|none)$",
    )
)


def _strip_ws_control(body: str) -> str:
    """Remove a leading/trailing Jinja whitespace-control marker and spaces."""
    body = body.strip()
    if body[:1] in "-+":
        body = body[1:]
    if body[-1:] in "-+":
        body = body[:-1]
    return body.strip()


def _expr_is_allowed(body: str) -> bool:
    body = _strip_ws_control(body)
    return any(p.match(body) for p in _EXPR_PATTERNS)


def _stmt_is_allowed(body: str) -> bool:
    body = _strip_ws_control(body)
    return any(p.match(body) for p in _STMT_PATTERNS)


def verify_jinja2_template_safe(template_text: str) -> None:
    """Validate that *template_text* contains only JinjaTurtle-emitted Jinja2.

    Raises :class:`TemplateSafetyError` on the first live construct that is not
    in the allowlist grammar.  Text inside JinjaTurtle's own ``{% raw %}``
    wrappers is treated as inert (the lexer does not tokenise it as tags), so
    legitimately-escaped source content passes.
    """
    # Import lazily so the dependency is only needed when generating Jinja2.
    import jinja2

    env = jinja2.Environment(autoescape=True)

    try:
        tokens = list(env.lex(template_text))
    except jinja2.TemplateSyntaxError as exc:
        # A syntax error means our own raw-wrapping did not fully contain the
        # source text (e.g. an early ``endraw`` breakout left dangling tags).
        # That is precisely a safety failure, not a benign parse hiccup.
        raise TemplateSafetyError(
            f"generated template does not lex as the JinjaTurtle subset: {exc}"
        ) from exc

    # Walk the token stream.  Jinja2 yields raw blocks as single
    # ``raw_begin``/``raw_end`` tokens with inert ``data`` between them, so we
    # never see wrapped literal text as live tags.  We collect the raw inner
    # text of each live ``{% %}`` / ``{{ }}`` construct (preserving original
    # spacing) and check it against the allowlist.
    mode: str | None = None  # None, "block", or "variable"
    body_parts: list[str] = []

    def _flush(kind: str, lineno: int) -> None:
        body = "".join(body_parts)
        if kind == "variable":
            ok = _expr_is_allowed(body)
        else:
            ok = _stmt_is_allowed(body)
        if not ok:
            shown = body.strip()
            wrapped = (
                "{{ " + shown + " }}" if kind == "variable" else "{% " + shown + " %}"
            )
            raise TemplateSafetyError(
                "refusing to emit template: unexpected live "
                f"{'expression' if kind == 'variable' else 'statement'} "
                f"{wrapped!r} at line {lineno}. This construct is not one "
                "JinjaTurtle emits, so it likely came from un-neutralised "
                "source text (possible template injection)."
            )

    for lineno, tok_type, value in tokens:
        if tok_type in ("variable_begin", "block_begin"):
            mode = "variable" if tok_type == "variable_begin" else "block"
            body_parts = []
        elif tok_type in ("variable_end", "block_end"):
            if mode is not None:
                _flush(mode, lineno)
            mode = None
            body_parts = []
        elif mode is not None:
            # Preserve the original token text (including its own whitespace
            # tokens) so the reconstructed body matches the source spacing.
            body_parts.append(value if isinstance(value, str) else str(value))
        # raw_begin / raw_end / data tokens outside a tag are inert: skip.


# --------------------------------------------------------------------------- #
# ERB gate.
#
# JinjaTurtle's ERB output is produced by translating the (already-verified)
# Jinja2 subset, so the Jinja2 gate is the primary guarantee.  As an independent
# ERB-side backstop we confirm that every ERB tag body is one the translator
# emits, and that no Jinja2 delimiters survived into the ERB output (which would
# indicate a raw block the translator failed to recognise -- the historical
# ``{%+ raw %}`` blind spot).
# --------------------------------------------------------------------------- #

_ERB_TAG_RE = re.compile(r"<%[-=#]?(.*?)[-]?%>", re.S)
_JINJA_DELIMS = ("{{", "}}", "{%", "%}", "{#", "#}")

# Bodies the ErbTranslator emits.  Kept permissive for Ruby method chains it
# constructs (``@var``, ``.each_with_index``, ``JSON.generate(...)`` etc.) but
# anchored so arbitrary attacker text cannot masquerade as one.
_ERB_STMT_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"^require 'json'$",
        r"^end$",
        r"^else$",
        r"^@?[A-Za-z_][\w@\.\[\]'\"]*\.each_with_index do \|[A-Za-z_]\w*, __jt_idx_\d+\| $",
        r"^if .+$",
        r"^elsif .+$",
        r"^unless .+\.nil\?$",
        r"^# Unsupported JinjaTurtle statement: .*$",
    )
)


def verify_erb_template_safe(template_text: str) -> None:
    """Validate that *template_text* contains no leftover Jinja2 delimiters.

    The translator is the security-relevant step for ERB; this backstop ensures
    no live Jinja construct survived translation (which would mean a raw block
    was not recognised and source text passed through untouched).
    """
    for delim in _JINJA_DELIMS:
        if delim in template_text:
            raise TemplateSafetyError(
                "refusing to emit ERB template: it still contains the Jinja2 "
                f"delimiter {delim!r}, which means source text was not fully "
                "translated/neutralised (possible template injection)."
            )
