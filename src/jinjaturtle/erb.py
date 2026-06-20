from __future__ import annotations

import re


def _safe_name(raw: str, *, fallback: str = "var") -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(raw or fallback)).strip("_").lower()
    text = re.sub(r"_+", "_", text)
    if not text:
        text = fallback
    if not re.match(r"^[a-z_]", text):
        text = f"{fallback}_{text}"
    return text


def puppet_class_name(raw: str) -> str:
    """Return a conservative Puppet class/Hiera namespace name."""

    text = _safe_name(raw, fallback="jinjaturtle")
    if not re.match(r"^[a-z]", text):
        text = f"jinjaturtle_{text}"
    return text


def _role_prefix_name(raw: str) -> str:
    return _safe_name(raw, fallback="jinjaturtle")


def puppet_local_var_name(
    role_prefix: str,
    jinja_var_name: str,
    *,
    puppet_class: str | None = None,
) -> str:
    """Map a generated JinjaTurtle variable to a Puppet class parameter.

    For the common case where ``--role-name php`` also means class ``php``, a
    generated Jinja variable such as ``php_memory_limit`` becomes Puppet local
    parameter ``memory_limit`` and Hiera key ``php::memory_limit``.

    Enroll sometimes needs a file-specific variable prefix to avoid collisions
    inside a generated module.  When ``puppet_class`` differs from
    ``role_prefix`` we keep the full generated variable name as the local
    parameter and only use ``puppet_class`` as the Hiera namespace.
    """

    var_name = _safe_name(jinja_var_name, fallback="value")
    prefix = _role_prefix_name(role_prefix)
    klass = puppet_class_name(puppet_class or role_prefix)
    if klass == prefix and var_name.startswith(prefix + "_"):
        stripped = var_name[len(prefix) + 1 :]
        return stripped or var_name
    return var_name


class ErbTranslator:
    """Translate the Jinja2 subset emitted by JinjaTurtle into Puppet ERB."""

    _TOKEN_RE = re.compile(r"({{.*?}}|{%.*?%})", re.S)

    def __init__(
        self,
        *,
        role_prefix: str,
        puppet_class: str | None = None,
        variable_names: set[str] | None = None,
    ) -> None:
        self.role_prefix = role_prefix
        self.puppet_class = puppet_class or role_prefix
        self.variable_names = set(variable_names or set())
        self.loop_stack: list[tuple[str, str, str]] = []
        self.needs_json = False

    def translate(self, template_text: str) -> str:
        parts = self._TOKEN_RE.split(template_text)
        out: list[str] = []
        for token in parts:
            if not token:
                continue
            if token.startswith("{{") and token.endswith("}}"):
                expr = token[2:-2].strip()
                out.append(f"<%= {self.expr_to_ruby(expr)} %>")
                continue
            if token.startswith("{%") and token.endswith("%}"):
                stmt = token[2:-2].strip()
                out.append(self.statement_to_erb(stmt))
                continue
            out.append(token)

        rendered = "".join(out)
        if self.needs_json and "require 'json'" not in rendered:
            rendered = "<% require 'json' -%>\n" + rendered
        return rendered

    def local_var(self, name: str) -> str:
        return puppet_local_var_name(
            self.role_prefix, name, puppet_class=self.puppet_class
        )

    def ruby_value(self, expr: str) -> str:
        expr = expr.strip()
        if expr in {"true", "True"}:
            return "true"
        if expr in {"false", "False"}:
            return "false"
        if expr in {"none", "None", "null"}:
            return "nil"
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", expr):
            if any(expr == loop_var for loop_var, _idx, _coll in self.loop_stack):
                return expr
            return f"@{self.local_var(expr)}"
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$", expr)
        if m:
            base, key = m.groups()
            if any(base == loop_var for loop_var, _idx, _coll in self.loop_stack):
                return f"{base}[{key!r}]"
            return f"@{self.local_var(base)}[{key!r}]"
        return expr

    def expr_to_ruby(self, expr: str) -> str:
        expr = expr.strip()

        # JinjaTurtle emits these YAML-preserving ternaries for booleans/nulls.
        m = re.match(
            r"^(['\"])(true|false)\1\s+if\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+else\s+(['\"])(true|false)\4$",
            expr,
        )
        if m:
            truthy = m.group(2)
            cond = self.ruby_value(m.group(3))
            falsy = m.group(5)
            return f"{cond} ? {truthy!r} : {falsy!r}"

        m = re.match(
            r"^(['\"])(null)\1\s+if\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+is\s+none\s+else\s+([A-Za-z_][A-Za-z0-9_\.]*)$",
            expr,
        )
        if m:
            value = self.ruby_value(m.group(3))
            fallback = self.ruby_value(m.group(4))
            return f"{value}.nil? ? 'null' : {fallback}"

        if "|" in expr:
            base, *filters = [part.strip() for part in expr.split("|")]
            ruby = self.ruby_value(base)
            for filt in filters:
                if filt.startswith("lower"):
                    ruby = f"{ruby}.to_s.downcase"
                elif filt.startswith("to_json") or filt.startswith("tojson"):
                    self.needs_json = True
                    if "indent" in filt:
                        ruby = f"JSON.pretty_generate({ruby})"
                    else:
                        ruby = f"JSON.generate({ruby})"
            return ruby

        return self.ruby_value(expr)

    def statement_to_erb(self, stmt: str) -> str:
        if stmt.endswith("-"):
            stmt = stmt[:-1].rstrip()

        if stmt.startswith("for "):
            m = re.match(
                r"^for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+([A-Za-z_][A-Za-z0-9_]*)$",
                stmt,
            )
            if m:
                loop_var, collection = m.groups()
                idx_var = f"__jt_idx_{len(self.loop_stack)}"
                collection_ruby = self.ruby_value(collection)
                self.loop_stack.append((loop_var, idx_var, collection_ruby))
                return f"<% {collection_ruby}.each_with_index do |{loop_var}, {idx_var}| -%>"

        if stmt == "endfor":
            if self.loop_stack:
                self.loop_stack.pop()
            return "<% end %>"

        if stmt.startswith("if "):
            cond = stmt[3:].strip()
            if cond == "not loop.last" and self.loop_stack:
                _loop_var, idx_var, collection_ruby = self.loop_stack[-1]
                return f"<% if {idx_var} < ({collection_ruby}.length - 1) -%>"
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_\.]*)\s+is\s+defined$", cond)
            if m:
                return f"<% unless {self.ruby_value(m.group(1))}.nil? -%>"
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_\.]*)\s+is\s+none$", cond)
            if m:
                return f"<% if {self.ruby_value(m.group(1))}.nil? -%>"
            return f"<% if {self.expr_to_ruby(cond)} -%>"

        if stmt == "else":
            return "<% else -%>"

        if stmt.startswith("elif "):
            return f"<% elsif {self.expr_to_ruby(stmt[5:].strip())} -%>"

        if stmt == "endif":
            return "<% end -%>"

        # Preserve unknown Jinja statements visibly as an ERB comment so the
        # generated template does not contain invalid Jinja syntax.
        return f"<%# Unsupported JinjaTurtle statement: {stmt} %>"


def translate_jinja2_to_erb(
    template_text: str,
    *,
    role_prefix: str,
    puppet_class: str | None = None,
    variable_names: set[str] | None = None,
) -> str:
    return ErbTranslator(
        role_prefix=role_prefix,
        puppet_class=puppet_class,
        variable_names=variable_names,
    ).translate(template_text)
