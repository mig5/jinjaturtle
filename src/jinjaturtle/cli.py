from __future__ import annotations

import argparse
import sys
from defusedxml import defuse_stdlib
from pathlib import Path

from . import j2
from .core import (
    parse_config,
    analyze_loops,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
    generate_puppet_hiera_yaml,
    generate_erb_template,
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
            "Supported: .toml, .yaml/.yml, .json, .ini/.cfg/.conf, .xml, ssh_config/sshd_config"
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
        choices=["ini", "json", "toml", "yaml", "xml", "postfix", "systemd", "ssh"],
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
        help="Path to write the generated config template. If omitted, template is printed to stdout.",
    )
    ap.add_argument(
        "--template-engine",
        choices=[j2.NAME, "erb"],
        default=j2.NAME,
        help="Template syntax to generate (default: jinja2). Use erb for Puppet templates.",
    )
    ap.add_argument(
        "--puppet-class",
        help=(
            "Puppet class/Hiera namespace to use with --template-engine erb. "
            "Defaults to --role-name. This lets tools use a file-specific "
            "variable prefix while writing Hiera keys under the real Puppet class."
        ),
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

        # Optionally translate folder-mode templates to ERB.  Folder mode keeps
        # the existing data shape; single-file mode below is the preferred
        # Puppet path because it can produce class-parameter Hiera keys.
        if args.template_engine == "erb":
            from .erb import translate_jinja2_to_erb

            for o in outputs:
                o.template = translate_jinja2_to_erb(
                    o.template,
                    role_prefix=args.role_name,
                    puppet_class=args.puppet_class or args.role_name,
                )

        template_ext = "erb" if args.template_engine == "erb" else j2.TEMPLATE_EXTENSION

        # Write templates
        if args.template_output:
            out_path = Path(args.template_output)
            if len(outputs) == 1 and not out_path.is_dir():
                out_path.write_text(outputs[0].template, encoding="utf-8")
            else:
                out_path.mkdir(parents=True, exist_ok=True)
                for o in outputs:
                    (out_path / f"config.{o.fmt}.{template_ext}").write_text(
                        o.template, encoding="utf-8"
                    )
        else:
            for o in outputs:
                name = (
                    f"config.{template_ext}"
                    if len(outputs) == 1
                    else f"config.{o.fmt}.{template_ext}"
                )
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

    if args.template_engine == "erb":
        ansible_yaml = generate_puppet_hiera_yaml(
            args.role_name,
            flat_items,
            loop_candidates,
            puppet_class=args.puppet_class or args.role_name,
        )
        template_str = generate_erb_template(
            fmt,
            parsed,
            args.role_name,
            original_text=config_text,
            loop_candidates=loop_candidates,
            flat_items=flat_items,
            puppet_class=args.puppet_class or args.role_name,
        )
    else:
        # Generate defaults YAML (with loop collections if detected)
        ansible_yaml = generate_ansible_yaml(
            args.role_name, flat_items, loop_candidates
        )

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
        print(
            "# config.erb"
            if args.template_engine == "erb"
            else f"# config.{j2.TEMPLATE_EXTENSION}"
        )
        print(template_str, end="")

    return 0


def main() -> None:
    """
    Console-script entry point.
    """
    sys.exit(_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
