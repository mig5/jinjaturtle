#!/usr/bin/env python3
"""
Side-by-side comparison of original vs regenerated config.

Usage:
    ./diff_configs.py tests/samples/foo.json
    ./diff_configs.py tests/samples/tom.toml --context 5
"""

import argparse
import sys
from pathlib import Path
import difflib
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


def colorize(text: str, color: str) -> str:
    """Add ANSI color codes."""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def side_by_side_diff(original: str, regenerated: str, width: int = 80):
    """Print side-by-side diff."""
    orig_lines = original.splitlines()
    regen_lines = regenerated.splitlines()

    # Calculate column width
    col_width = width // 2 - 3

    print(
        colorize("ORIGINAL".center(col_width), "blue")
        + " | "
        + colorize("REGENERATED".center(col_width), "green")
    )
    print("-" * col_width + "-+-" + "-" * col_width)

    max_lines = max(len(orig_lines), len(regen_lines))

    for i in range(max_lines):
        orig_line = orig_lines[i] if i < len(orig_lines) else ""
        regen_line = regen_lines[i] if i < len(regen_lines) else ""

        # Truncate if too long
        if len(orig_line) > col_width - 2:
            orig_line = orig_line[: col_width - 5] + "..."
        if len(regen_line) > col_width - 2:
            regen_line = regen_line[: col_width - 5] + "..."

        # Color lines if different
        if orig_line != regen_line:
            orig_display = colorize(orig_line.ljust(col_width), "red")
            regen_display = colorize(regen_line.ljust(col_width), "green")
        else:
            orig_display = orig_line.ljust(col_width)
            regen_display = regen_line.ljust(col_width)

        print(f"{orig_display} | {regen_display}")


def unified_diff(original: str, regenerated: str, filename: str, context: int = 3):
    """Print unified diff."""
    orig_lines = original.splitlines(keepends=True)
    regen_lines = regenerated.splitlines(keepends=True)

    diff = difflib.unified_diff(
        orig_lines,
        regen_lines,
        fromfile=f"{filename} (original)",
        tofile=f"{filename} (regenerated)",
        n=context,
    )

    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            print(colorize(line.rstrip(), "blue"))
        elif line.startswith("@@"):
            print(colorize(line.rstrip(), "cyan"))
        elif line.startswith("+"):
            print(colorize(line.rstrip(), "green"))
        elif line.startswith("-"):
            print(colorize(line.rstrip(), "red"))
        else:
            print(line.rstrip())


def main():
    parser = argparse.ArgumentParser(
        description="Compare original config with regenerated version",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("file", type=Path, help="Config file to check")

    parser.add_argument(
        "--mode",
        choices=["side-by-side", "unified", "both"],
        default="both",
        help="Comparison mode (default: both)",
    )

    parser.add_argument(
        "--context",
        type=int,
        default=3,
        help="Number of context lines for unified diff (default: 3)",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=160,
        help="Terminal width for side-by-side (default: 160)",
    )

    args = parser.parse_args()

    if not args.file.exists():
        print(colorize(f"❌ File not found: {args.file}", "red"))
        return 1

    print(colorize(f"\n{'=' * 80}", "blue"))
    print(colorize(f"  Comparing: {args.file}", "blue"))
    print(colorize(f"{'=' * 80}\n", "blue"))

    # Read and regenerate
    try:
        original_text = args.file.read_text()

        fmt, parsed = parse_config(args.file)
        loop_candidates = analyze_loops(fmt, parsed)
        flat_items = flatten_config(fmt, parsed, loop_candidates)

        ansible_yaml = generate_ansible_yaml("app", flat_items, loop_candidates)
        template = generate_jinja2_template(
            fmt, parsed, "app", original_text, loop_candidates
        )

        variables = yaml.safe_load(ansible_yaml)
        env = Environment(undefined=StrictUndefined)
        jinja_template = env.from_string(template)
        regenerated_text = jinja_template.render(variables)

        # Check if identical
        if original_text.strip() == regenerated_text.strip():
            print(colorize("✅ Files are IDENTICAL (text comparison)\n", "green"))
        else:
            # Show diff
            if args.mode in ("unified", "both"):
                print(colorize("\n--- UNIFIED DIFF ---\n", "yellow"))
                unified_diff(
                    original_text, regenerated_text, args.file.name, args.context
                )

            if args.mode in ("side-by-side", "both"):
                print(colorize("\n--- SIDE-BY-SIDE COMPARISON ---\n", "yellow"))
                side_by_side_diff(original_text, regenerated_text, args.width)

        # Try semantic comparison
        print(colorize(f"\n{'=' * 80}", "cyan"))
        print(colorize("  Semantic Comparison", "cyan"))
        print(colorize(f"{'=' * 80}", "cyan"))

        try:
            if fmt == "json":
                import json

                if json.loads(original_text) == json.loads(regenerated_text):
                    print(colorize("✅ JSON data structures are IDENTICAL", "green"))
                else:
                    print(colorize("⚠️  JSON data structures DIFFER", "yellow"))
            elif fmt == "yaml":
                if yaml.safe_load(original_text) == yaml.safe_load(regenerated_text):
                    print(colorize("✅ YAML data structures are IDENTICAL", "green"))
                else:
                    print(colorize("⚠️  YAML data structures DIFFER", "yellow"))
            elif fmt == "toml":
                try:
                    import tomllib
                except Exception:
                    import tomli as tomllib
                if tomllib.loads(original_text) == tomllib.loads(regenerated_text):
                    print(colorize("✅ TOML data structures are IDENTICAL", "green"))
                else:
                    print(colorize("⚠️  TOML data structures DIFFER", "yellow"))
        except Exception as e:
            print(colorize(f"ℹ️  Could not compare semantically: {e}", "yellow"))

    except Exception as e:
        print(colorize(f"❌ ERROR: {e}", "red"))
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
