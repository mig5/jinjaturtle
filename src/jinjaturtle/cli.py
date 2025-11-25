from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import (
    parse_config,
    flatten_config,
    generate_defaults_yaml,
    generate_template,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="jinjaturtle",
        description="Convert a config file into an Ansible defaults file and Jinja2 template.",
    )
    ap.add_argument(
        "config",
        help="Path to the source configuration file (TOML or INI-style).",
    )
    ap.add_argument(
        "-r",
        "--role-name",
        required=True,
        help="Ansible role name, used as variable prefix (e.g. cometbft).",
    )
    ap.add_argument(
        "-f",
        "--format",
        choices=["ini", "toml"],
        help="Force config format instead of auto-detecting from filename.",
    )
    ap.add_argument(
        "-d",
        "--defaults-output",
        help="Path to write defaults/main.yml. If omitted, defaults YAML is printed to stdout.",
    )
    ap.add_argument(
        "-t",
        "--template-output",
        help="Path to write the Jinja2 config template. If omitted, template is printed to stdout.",
    )
    return ap


def _main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    fmt, parsed = parse_config(config_path, args.format)
    flat_items = flatten_config(fmt, parsed)
    defaults_yaml = generate_defaults_yaml(args.role_name, flat_items)
    template_str = generate_template(fmt, parsed, args.role_name)

    if args.defaults_output:
        Path(args.defaults_output).write_text(defaults_yaml, encoding="utf-8")
    else:
        print("# defaults/main.yml")
        print(defaults_yaml, end="")

    if args.template_output:
        Path(args.template_output).write_text(template_str, encoding="utf-8")
    else:
        print("# config.j2")
        print(template_str, end="")

    return 0


def main() -> None:
    """
    Console-script entry point.

    Defined in pyproject.toml as:
      jinjaturtle = jinjaturtle.cli:main
    """
    raise SystemExit(_main(sys.argv[1:]))
