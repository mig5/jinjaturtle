#!/usr/bin/env python3
"""
Regenerate config files and save all intermediate files.

Creates:
  - original.{ext}
  - defaults/main.yml
  - templates/config.j2
  - regenerated.{ext}

Usage:
    ./regenerate.py tests/samples/foo.json
    ./regenerate.py tests/samples/tom.toml --output-dir tmp/toml_test
"""

import argparse
import sys
from pathlib import Path
import yaml
from jinja2 import Environment, StrictUndefined

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from jinjaturtle.core import (
    parse_config,
    analyze_loops,
    flatten_config,
    generate_ansible_yaml,
    generate_jinja2_template,
)


def regenerate_and_save(config_file: Path, output_dir: Path, role_prefix: str = "app"):
    """
    Regenerate config and save all intermediate files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read original
    original_text = config_file.read_text()
    fmt, parsed = parse_config(config_file)

    # Determine extension
    ext = config_file.suffix

    # Save original
    original_out = output_dir / f"original{ext}"
    original_out.write_text(original_text)
    print(f"📄 Saved: {original_out}")

    # Generate Ansible files
    loop_candidates = analyze_loops(fmt, parsed)
    flat_items = flatten_config(fmt, parsed, loop_candidates)

    ansible_yaml = generate_ansible_yaml(role_prefix, flat_items, loop_candidates)
    template = generate_jinja2_template(
        fmt, parsed, role_prefix, original_text, loop_candidates
    )

    # Save Ansible YAML
    defaults_dir = output_dir / "defaults"
    defaults_dir.mkdir(exist_ok=True)
    defaults_file = defaults_dir / "main.yml"
    defaults_file.write_text(ansible_yaml)
    print(f"📄 Saved: {defaults_file}")

    # Save template
    templates_dir = output_dir / "templates"
    templates_dir.mkdir(exist_ok=True)
    template_file = templates_dir / "config.j2"
    template_file.write_text(template)
    print(f"📄 Saved: {template_file}")

    # Render template
    variables = yaml.safe_load(ansible_yaml)
    env = Environment(undefined=StrictUndefined)
    jinja_template = env.from_string(template)
    regenerated_text = jinja_template.render(variables)

    # Save regenerated
    regenerated_out = output_dir / f"regenerated{ext}"
    regenerated_out.write_text(regenerated_text)
    print(f"📄 Saved: {regenerated_out}")

    # Summary
    print(f"\n✅ All files saved to: {output_dir}")
    print("\n📊 Statistics:")
    print(f"   Format: {fmt}")
    print(f"   Loop candidates: {len(loop_candidates)}")
    if loop_candidates:
        print("   Loops detected:")
        for c in loop_candidates:
            print(f"     - {'.'.join(c.path)}: {len(c.items)} items")

    # Check if identical
    if original_text.strip() == regenerated_text.strip():
        print("\n✅ Original and regenerated are IDENTICAL (text comparison)")
    else:
        print("\n⚠️  Original and regenerated differ in whitespace/formatting")
        print(f"   Run: diff {original_out} {regenerated_out}")

    return output_dir


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate config and save all intermediate files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s tests/samples/foo.json
  %(prog)s tests/samples/tom.toml -o tmp/toml_output
  %(prog)s tests/samples/bar.yaml --role-prefix myapp
        """,
    )

    parser.add_argument("file", type=Path, help="Config file to process")

    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Output directory (default: regenerated_<filename>)",
    )

    parser.add_argument(
        "-r",
        "--role-prefix",
        default="app",
        help="Ansible role prefix for variables (default: app)",
    )

    args = parser.parse_args()

    if not args.file.exists():
        print(f"❌ File not found: {args.file}")
        return 1

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = Path(f"regenerated_{args.file.stem}")

    print(f"🔄 Regenerating: {args.file}")
    print(f"📁 Output directory: {output_dir}")
    print(f"🏷️  Role prefix: {args.role_prefix}\n")

    try:
        regenerate_and_save(args.file, output_dir, args.role_prefix)
        return 0
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
