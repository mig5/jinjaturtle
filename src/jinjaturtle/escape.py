from __future__ import annotations

"""Neutralise template metacharacters in text copied verbatim from source files.

JinjaTurtle preserves formatting by copying parts of the *original* config file
straight into the generated template: comments, blank lines, section headers,
and any line it does not recognise as ``key = value``.  Config *values* are
always replaced with ``{{ var }}`` placeholders and parked in the defaults data,
so a payload inside a value is inert.  Verbatim text is different: if the source
contains ``{{ ... }}``, ``{% ... %}`` or ``{# ... #}`` (Jinja2), or ``<%= %>`` /
``<% %>`` (ERB), that text becomes *live template code* in the output and is
executed when Ansible later renders the template.

Because JinjaTurtle is frequently fed harvested, attacker-influenceable config
(hostnames, banners, GECOS-derived comments, "Managed by" notes), this is a
template-injection / SSTI vector that can lead to remote code execution on the
configuration-management control node.

The functions here render those metacharacters as literal text so they survive a
later template render as the characters the source author actually wrote, rather
than as executable template syntax.

Design notes:
  * We only ever escape text that originates from the *source file*.  We never
    pass JinjaTurtle's own generated placeholders (``{{ role_var }}``) through
    these helpers, so the placeholders keep working.
  * Jinja2 literal text is wrapped in a single ``{% raw %} ... {% endraw %}``
    block.  ``raw`` disables *all* tag interpretation inside it -- expressions,
    statements and ``{# #}`` comments alike -- so one wrap neutralises every
    Jinja construct.  The only way to break out of a raw block is a literal
    ``{% endraw %}`` in the source, so we defang the token ``endraw`` (in any
    internal spacing) before wrapping.
"""

import re

# Jinja2 delimiters we must neutralise.  Engine-default; JinjaTurtle never
# configures custom delimiters.
_JINJA_MARKERS = ("{{", "}}", "{%", "%}", "{#", "#}")

# ERB delimiters.  Longer markers first so "<%=" matches before "<%".
_ERB_OPEN_MARKERS = ("<%=", "<%-", "<%#", "<%")
_ERB_CLOSE_MARKERS = ("-%>", "%>")

# Matches a Jinja2 endraw tag in any internal spacing and with any
# whitespace-control marker on either side.  Jinja2 accepts "-", "+", or no
# marker adjacent to the "%}"/"{%" of a block tag (e.g. "{%endraw%}",
# "{%  endraw  %}", "{%- endraw -%}", "{%+ endraw +%}"), and ALL of these close
# a raw block.  The control marker must be matched so a "{%+ endraw %}" in
# attacker-influenced source text cannot survive defanging and break out of our
# {% raw %} wrapper.  [-+]? appears on both sides accordingly.
_ENDRAW_RE = re.compile(r"{%[-+]?\s*endraw\s*[-+]?%}")

# Sentinel inserted between "end" and "raw" to break the endraw keyword without
# changing the visible characters.  We use a Jinja comment-free approach: insert
# the two halves across a raw boundary so the literal text still reads "endraw"
# to a human but is never a valid tag.  See escape_jinja_literal for usage.


def contains_jinja_markup(text: str) -> bool:
    """Return True if *text* contains any Jinja2 delimiter."""
    return any(m in text for m in _JINJA_MARKERS)


def _defang_endraw(text: str) -> str:
    """Rewrite any literal ``{% endraw %}`` so it cannot close our raw wrapper.

    We turn each endraw tag into ``{% endraw %}{{ '{% endraw %}' }}{% raw %}``...
    no -- that would re-introduce live tags.  Instead we keep everything literal:
    we break the keyword by emitting the tag's text in two raw segments split
    inside the word ``endraw``.  The result, when later rendered, reproduces the
    exact original characters ``{% endraw %}`` while never being a parseable tag.
    """

    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        # Split the keyword "endraw" as "end" + "raw"; close and reopen the raw
        # block between them.  Each half is plain text inside a raw block, so the
        # reconstructed output is byte-identical to the original tag, but at no
        # point does the token "{% endraw %}" exist contiguously to close raw.
        idx = tag.lower().index("endraw")
        head = tag[: idx + 3]  # up to and including "end"
        tail = tag[idx + 3 :]  # "raw...%}"
        return f"{head}{{% endraw %}}{{% raw %}}{tail}"

    return _ENDRAW_RE.sub(_replace, text)


def escape_jinja_literal(text: str) -> str:
    """Make *text* render as literal characters under a later Jinja2 render.

    Text with no Jinja metacharacters is returned unchanged so the common case
    stays byte-for-byte identical to the source.  Otherwise the text is wrapped
    in a single ``{% raw %}`` block, with any embedded ``endraw`` defanged.
    """
    if not text or not contains_jinja_markup(text):
        return text
    return "{% raw %}" + _defang_endraw(text) + "{% endraw %}"
