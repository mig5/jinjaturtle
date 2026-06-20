from __future__ import annotations

from typing import Any

NAME = "jinja2"
TEMPLATE_EXTENSION = "j2"
JSON_VALUE_FILTER = "to_json(ensure_ascii=False)"


def expression(value: str) -> str:
    """Return a Jinja2 output expression for an already-built expression body."""
    return f"{{{{ {value} }}}}"


def variable(name: str) -> str:
    """Return a Jinja2 output expression for a variable name."""
    return expression(name)


def quoted_variable(name: str, quote: str = '"') -> str:
    """Return a quoted Jinja2 variable placeholder."""
    return f"{quote}{variable(name)}{quote}"


def filtered(value: str, filter_expression: str) -> str:
    """Return a Jinja2 output expression with one filter expression applied."""
    return expression(f"{value} | {filter_expression}")


def lower(value: str) -> str:
    """Return a Jinja2 expression that renders a value through ``| lower``."""
    return filtered(value, "lower")


def to_json(
    value: str, *, indent: int | None = None, ensure_ascii: bool = False
) -> str:
    """Return a Jinja2 expression using Ansible's ``to_json`` filter."""
    args: list[str] = []
    if indent is not None:
        args.append(f"indent={indent}")
    args.append(f"ensure_ascii={ensure_ascii}")
    return filtered(value, f"to_json({', '.join(args)})")


def statement(value: str) -> str:
    """Return a Jinja2 statement tag for an already-built statement body."""
    return f"{{% {value} %}}"


def if_defined(name: str) -> str:
    return statement(f"if {name} is defined")


def if_not_loop_last() -> str:
    return statement("if not loop.last")


def endif() -> str:
    return statement("endif")


def for_start(item_var: str, collection_var: str) -> str:
    return statement(f"for {item_var} in {collection_var}")


def for_end() -> str:
    return statement("endfor")


def yaml_scalar_expression(var_name: str, raw_value: str | None = None) -> str:
    """Return a YAML-safe Jinja2 expression for a scalar value."""
    raw = (raw_value or "").strip().lower()
    if raw in {"true", "false"}:
        return expression(f"'true' if {var_name} else 'false'")
    if raw in {"null", "~"}:
        return expression(f"'null' if {var_name} is none else {var_name}")
    return variable(var_name)


def yaml_value_expression(value_expr: str, sample_value: Any | None = None) -> str:
    """Return a YAML-safe Jinja2 expression for a possibly typed sample value."""
    if isinstance(sample_value, bool):
        return expression(f"'true' if {value_expr} else 'false'")
    if sample_value is None:
        return expression(f"'null' if {value_expr} is none else {value_expr}")
    return expression(value_expr)
