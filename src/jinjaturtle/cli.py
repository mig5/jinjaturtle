from __future__ import annotations

import argparse
import sys
from defusedxml import defuse_stdlib
from pathlib import Path

from .core import (
    parse_config,
    analyze_loops,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)

from .multi import process_directory


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="jinjaturtle",
        description="Convert a config file into an Ansible defaults file and Jinja2 template.",
    )
    ap.add_argument(
        "config",
        help=(
            "Path to a config file OR a folder containing supported config files. "
            "Supported: .toml, .yaml/.yml, .json, .ini/.cfg/.conf, .xml"
        ),
    )
    ap.add_argument(
        "-r",
        "--role-name",
        default="jinjaturtle",
        help="Ansible role name, used as variable prefix (default: jinjaturtle).",
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="When CONFIG is a folder, recurse into subfolders.",
    )
    ap.add_argument(
        "-f",
        "--format",
        choices=["ini", "json", "toml", "yaml", "xml"],
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
    defuse_stdlib()
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    config_path = Path(args.config)

    # Folder mode
    if config_path.is_dir():
        defaults_yaml, outputs = process_directory(
            config_path, args.recursive, args.role_name
        )

        # Write defaults
        if args.defaults_output:
            Path(args.defaults_output).write_text(defaults_yaml, encoding="utf-8")
        else:
            print("# defaults/main.yml")
            print(defaults_yaml, end="")

        # Write templates
        if args.template_output:
            out_path = Path(args.template_output)
            if len(outputs) == 1 and not out_path.is_dir():
                out_path.write_text(outputs[0].template, encoding="utf-8")
            else:
                out_path.mkdir(parents=True, exist_ok=True)
                for o in outputs:
                    (out_path / f"config.{o.fmt}.j2").write_text(
                        o.template, encoding="utf-8"
                    )
        else:
            for o in outputs:
                name = "config.j2" if len(outputs) == 1 else f"config.{o.fmt}.j2"
                print(f"# {name}")
                print(o.template, end="")

        return 0

    # Single-file mode (existing behaviour)
    config_text = config_path.read_text(encoding="utf-8")

    # Parse the config
    fmt, parsed = parse_config(config_path, args.format)

    # Analyze for loops
    loop_candidates = analyze_loops(fmt, parsed)

    # Flatten config (excluding loop paths if loops are detected)
    flat_items = flatten_config(fmt, parsed, loop_candidates)

    # Generate defaults YAML (with loop collections if detected)
    ansible_yaml = generate_ansible_yaml(args.role_name, flat_items, loop_candidates)

    # Generate template (with loops if detected)
    template_str = generate_jinja2_template(
        fmt,
        parsed,
        args.role_name,
        original_text=config_text,
        loop_candidates=loop_candidates,
    )

    if args.defaults_output:
        Path(args.defaults_output).write_text(ansible_yaml, encoding="utf-8")
    else:
        print("# defaults/main.yml")
        print(ansible_yaml, end="")

    if args.template_output:
        Path(args.template_output).write_text(template_str, encoding="utf-8")
    else:
        print("# config.j2")
        print(template_str, end="")

    return 0


def main() -> None:
    """
    Console-script entry point.
    """
    _main(sys.argv[1:])
