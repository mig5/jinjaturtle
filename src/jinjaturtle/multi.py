from __future__ import annotations

"""Directory / multi-file processing.

Folder mode:
  * discover supported config files under a directory (optionally recursively)
  * group them by detected format
  * generate one *union* Jinja2 template per format
  * generate a single defaults YAML containing a list of per-file values

The union templates use `{% if ... is defined %}` blocks for paths that are
missing in some input files ("option B"), so missing keys/sections/elements are
omitted rather than rendered as empty values.

Notes:
  * If the folder contains *multiple* formats, we generate one template per
    format (e.g. config.yaml.j2, config.xml.j2) and emit one list variable per
    format in the defaults YAML.
  * JSON union templates are emitted using a simple `{{ data | tojson }}`
    approach to avoid comma-management complexity for optional keys.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET  # nosec

from .core import dump_yaml, flatten_config, make_var_name, parse_config
from .handlers.xml import XmlHandler


SUPPORTED_SUFFIXES: dict[str, set[str]] = {
    "toml": {".toml"},
    "yaml": {".yaml", ".yml"},
    "json": {".json"},
    "ini": {".ini", ".cfg", ".conf"},
    "xml": {".xml"},
}


def is_supported_file(path: Path) -> bool:
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    for exts in SUPPORTED_SUFFIXES.values():
        if suffix in exts:
            return True
    return False


def iter_supported_files(root: Path, recursive: bool) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(str(root))
    if root.is_file():
        return [root] if is_supported_file(root) else []
    if not root.is_dir():
        return []

    it = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in it if is_supported_file(p)]
    files.sort()
    return files


def defined_var_name(role_prefix: str, path: Iterable[str]) -> str:
    """Presence marker var for a container path."""
    return make_var_name(role_prefix, ("defined",) + tuple(path))


def _is_scalar(obj: Any) -> bool:
    return not isinstance(obj, (dict, list))


def _merge_union(a: Any, b: Any) -> Any:
    """Merge two parsed objects into a union structure.

    - dicts: union keys, recursive
    - lists: max length, merge by index
    - scalars: keep the first (as a representative sample)
    """
    if isinstance(a, dict) and isinstance(b, dict):
        out: dict[str, Any] = {}
        for k in b.keys():
            if k not in out and k not in a:
                # handled later
                pass
        # preserve insertion order roughly: keys from a, then new keys from b
        for k in a.keys():
            out[k] = _merge_union(a.get(k), b.get(k)) if k in b else a.get(k)
        for k in b.keys():
            if k not in out:
                out[k] = b.get(k)
        return out
    if isinstance(a, list) and isinstance(b, list):
        n = max(len(a), len(b))
        out_list: list[Any] = []
        for i in range(n):
            if i < len(a) and i < len(b):
                out_list.append(_merge_union(a[i], b[i]))
            elif i < len(a):
                out_list.append(a[i])
            else:
                out_list.append(b[i])
        return out_list
    # different types or scalar
    return a if a is not None else b


def _collect_dict_like_paths(
    obj: Any,
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    """Return (container_paths, leaf_paths) for dict/list structures."""
    containers: set[tuple[str, ...]] = set()
    leaves: set[tuple[str, ...]] = set()

    def walk(o: Any, path: tuple[str, ...]) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                kp = path + (str(k),)
                containers.add(kp)
                walk(v, kp)
            return
        if isinstance(o, list):
            for i, v in enumerate(o):
                ip = path + (str(i),)
                containers.add(ip)
                walk(v, ip)
            return
        leaves.add(path)

    walk(obj, ())
    return containers, leaves


def _yaml_scalar_placeholder(
    role_prefix: str, path: tuple[str, ...], sample: Any
) -> str:
    var = make_var_name(role_prefix, path)
    if isinstance(sample, str):
        return f'"{{{{ {var} }}}}"'
    return f"{{{{ {var} }}}}"


def _yaml_render_union(
    role_prefix: str,
    union_obj: Any,
    optional_containers: set[tuple[str, ...]],
    indent: int = 0,
    path: tuple[str, ...] = (),
    in_list: bool = False,
) -> list[str]:
    """Render YAML for union_obj with conditionals for optional containers."""
    lines: list[str] = []
    ind = " " * indent

    if isinstance(union_obj, dict):
        for key, val in union_obj.items():
            key_path = path + (str(key),)
            cond_var = (
                defined_var_name(role_prefix, key_path)
                if key_path in optional_containers
                else None
            )

            if _is_scalar(val) or val is None:
                value = _yaml_scalar_placeholder(role_prefix, key_path, val)
                if cond_var:
                    lines.append(f"{ind}{{% if {cond_var} is defined %}}")
                lines.append(f"{ind}{key}: {value}")
                if cond_var:
                    lines.append(f"{ind}{{% endif %}}")
            else:
                if cond_var:
                    lines.append(f"{ind}{{% if {cond_var} is defined %}}")
                lines.append(f"{ind}{key}:")
                lines.extend(
                    _yaml_render_union(
                        role_prefix,
                        val,
                        optional_containers,
                        indent=indent + 2,
                        path=key_path,
                        in_list=False,
                    )
                )
                if cond_var:
                    lines.append(f"{ind}{{% endif %}}")
        return lines

    if isinstance(union_obj, list):
        for i, item in enumerate(union_obj):
            item_path = path + (str(i),)
            cond_var = (
                defined_var_name(role_prefix, item_path)
                if item_path in optional_containers
                else None
            )

            if _is_scalar(item) or item is None:
                value = _yaml_scalar_placeholder(role_prefix, item_path, item)
                if cond_var:
                    lines.append(f"{ind}{{% if {cond_var} is defined %}}")
                lines.append(f"{ind}- {value}")
                if cond_var:
                    lines.append(f"{ind}{{% endif %}}")
            elif isinstance(item, dict):
                if cond_var:
                    lines.append(f"{ind}{{% if {cond_var} is defined %}}")
                # First line: list marker with first key if possible
                first = True
                for k, v in item.items():
                    kp = item_path + (str(k),)
                    k_cond = (
                        defined_var_name(role_prefix, kp)
                        if kp in optional_containers
                        else None
                    )
                    if _is_scalar(v) or v is None:
                        value = _yaml_scalar_placeholder(role_prefix, kp, v)
                        if first:
                            if k_cond:
                                lines.append(f"{ind}{{% if {k_cond} is defined %}}")
                            lines.append(f"{ind}- {k}: {value}")
                            if k_cond:
                                lines.append(f"{ind}{{% endif %}}")
                            first = False
                        else:
                            if k_cond:
                                lines.append(f"{ind}  {{% if {k_cond} is defined %}}")
                            lines.append(f"{ind}  {k}: {value}")
                            if k_cond:
                                lines.append(f"{ind}  {{% endif %}}")
                    else:
                        # nested
                        if first:
                            if k_cond:
                                lines.append(f"{ind}{{% if {k_cond} is defined %}}")
                            lines.append(f"{ind}- {k}:")
                            lines.extend(
                                _yaml_render_union(
                                    role_prefix,
                                    v,
                                    optional_containers,
                                    indent=indent + 4,
                                    path=kp,
                                )
                            )
                            if k_cond:
                                lines.append(f"{ind}{{% endif %}}")
                            first = False
                        else:
                            if k_cond:
                                lines.append(f"{ind}  {{% if {k_cond} is defined %}}")
                            lines.append(f"{ind}  {k}:")
                            lines.extend(
                                _yaml_render_union(
                                    role_prefix,
                                    v,
                                    optional_containers,
                                    indent=indent + 4,
                                    path=kp,
                                )
                            )
                            if k_cond:
                                lines.append(f"{ind}  {{% endif %}}")
                if first:
                    # empty dict item
                    lines.append(f"{ind}- {{}}")
                if cond_var:
                    lines.append(f"{ind}{{% endif %}}")
            else:
                # list of lists - emit as scalar-ish fallback
                value = f"{{{{ {make_var_name(role_prefix, item_path)} }}}}"
                if cond_var:
                    lines.append(f"{ind}{{% if {cond_var} is defined %}}")
                lines.append(f"{ind}- {value}")
                if cond_var:
                    lines.append(f"{ind}{{% endif %}}")
        return lines

    # scalar at root
    value = _yaml_scalar_placeholder(role_prefix, path, union_obj)
    if in_list:
        lines.append(f"{ind}- {value}")
    else:
        lines.append(f"{ind}{value}")
    return lines


def _toml_render_union(
    role_prefix: str,
    union_obj: dict[str, Any],
    optional_containers: set[tuple[str, ...]],
) -> str:
    """Render TOML union template with optional tables/keys."""
    lines: list[str] = []

    def emit_kv(path: tuple[str, ...], key: str, value: Any) -> None:
        var_name = make_var_name(role_prefix, path + (key,))
        cond = (
            defined_var_name(role_prefix, path + (key,))
            if (path + (key,)) in optional_containers
            else None
        )
        if cond:
            lines.append(f"{{% if {cond} is defined %}}")
        if isinstance(value, str):
            lines.append(f'{key} = "{{{{ {var_name} }}}}"')
        elif isinstance(value, bool):
            lines.append(f"{key} = {{{{ {var_name} | lower }}}}")
        else:
            lines.append(f"{key} = {{{{ {var_name} }}}}")
        if cond:
            lines.append("{% endif %}")

    def walk(obj: dict[str, Any], path: tuple[str, ...]) -> None:
        if path:
            cond = (
                defined_var_name(role_prefix, path)
                if path in optional_containers
                else None
            )
            if cond:
                lines.append(f"{{% if {cond} is defined %}}")
            lines.append(f"[{'.'.join(path)}]")

        scalar_items = {k: v for k, v in obj.items() if not isinstance(v, dict)}
        nested_items = {k: v for k, v in obj.items() if isinstance(v, dict)}

        for k, v in scalar_items.items():
            emit_kv(path, str(k), v)

        if scalar_items:
            lines.append("")

        for k, v in nested_items.items():
            walk(v, path + (str(k),))

        if path and (path in optional_containers):
            lines.append("{% endif %}")
            lines.append("")

    # root scalars
    root_scalars = {k: v for k, v in union_obj.items() if not isinstance(v, dict)}
    for k, v in root_scalars.items():
        emit_kv((), str(k), v)
    if root_scalars:
        lines.append("")
    for k, v in union_obj.items():
        if isinstance(v, dict):
            walk(v, (str(k),))

    return "\n".join(lines).rstrip() + "\n"


def _ini_union_and_presence(
    parsers: list[configparser.ConfigParser],
) -> tuple[configparser.ConfigParser, set[str], set[tuple[str, str]]]:
    """Build a union ConfigParser and compute optional sections/keys."""
    union = configparser.ConfigParser()
    union.optionxform = str  # noqa

    section_sets: list[set[str]] = []
    key_sets: list[set[tuple[str, str]]] = []

    for p in parsers:
        sections = set(p.sections())
        section_sets.append(sections)
        keys: set[tuple[str, str]] = set()
        for s in p.sections():
            for k, _ in p.items(s, raw=True):
                keys.add((s, k))
        key_sets.append(keys)

        for s in p.sections():
            if not union.has_section(s):
                union.add_section(s)
            for k, v in p.items(s, raw=True):
                if not union.has_option(s, k):
                    union.set(s, k, v)

    if not section_sets:
        return union, set(), set()

    sec_union = set().union(*section_sets)
    sec_inter = set.intersection(*section_sets)
    optional_sections = sec_union - sec_inter

    key_union = set().union(*key_sets)
    key_inter = set.intersection(*key_sets)
    optional_keys = key_union - key_inter

    return union, optional_sections, optional_keys


def _ini_render_union(
    role_prefix: str,
    union: configparser.ConfigParser,
    optional_sections: set[str],
    optional_keys: set[tuple[str, str]],
) -> str:
    lines: list[str] = []
    for section in union.sections():
        sec_cond = (
            defined_var_name(role_prefix, (section,))
            if section in optional_sections
            else None
        )
        if sec_cond:
            lines.append(f"{{% if {sec_cond} is defined %}}")
        lines.append(f"[{section}]")
        for key, raw_val in union.items(section, raw=True):
            path = (section, key)
            var = make_var_name(role_prefix, path)
            key_cond = (
                defined_var_name(role_prefix, path) if path in optional_keys else None
            )
            v = (raw_val or "").strip()
            quoted = len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}
            if key_cond:
                lines.append(f"{{% if {key_cond} is defined %}}")
            if quoted:
                lines.append(f'{key} = "{{{{ {var} }}}}"')
            else:
                lines.append(f"{key} = {{{{ {var} }}}}")
            if key_cond:
                lines.append("{% endif %}")
        lines.append("")
        if sec_cond:
            lines.append("{% endif %}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _xml_collect_paths(
    root: ET.Element,
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    """Return (element_paths, leaf_paths) based on XmlHandler's flatten rules."""
    element_paths: set[tuple[str, ...]] = set()
    leaf_paths: set[tuple[str, ...]] = set()

    def walk(elem: ET.Element, path: tuple[str, ...]) -> None:
        element_paths.add(path)

        for attr in elem.attrib:
            leaf_paths.add(path + (f"@{attr}",))

        children = [c for c in list(elem) if isinstance(c.tag, str)]
        text = (elem.text or "").strip()
        if text:
            if not elem.attrib and not children:
                leaf_paths.add(path)
            else:
                leaf_paths.add(path + ("value",))

        counts = Counter(child.tag for child in children)
        index_counters: dict[str, int] = defaultdict(int)
        for child in children:
            tag = child.tag
            if counts[tag] > 1:
                idx = index_counters[tag]
                index_counters[tag] += 1
                child_path = path + (tag, str(idx))
            else:
                child_path = path + (tag,)
            walk(child, child_path)

    walk(root, ())
    return element_paths, leaf_paths


def _xml_merge_union(base: ET.Element, other: ET.Element) -> None:
    """Merge other into base in-place."""
    # attributes
    for k, v in other.attrib.items():
        if k not in base.attrib:
            base.set(k, v)

    # text
    if (base.text is None or not base.text.strip()) and (
        other.text and other.text.strip()
    ):
        base.text = other.text

    # children
    base_children = [c for c in list(base) if isinstance(c.tag, str)]
    other_children = [c for c in list(other) if isinstance(c.tag, str)]

    base_by_tag: dict[str, list[ET.Element]] = defaultdict(list)
    other_by_tag: dict[str, list[ET.Element]] = defaultdict(list)
    for c in base_children:
        base_by_tag[c.tag].append(c)
    for c in other_children:
        other_by_tag[c.tag].append(c)

    # preserve base ordering; append new tags at end
    seen_tags = set(base_by_tag.keys())
    tag_order = [c.tag for c in base_children if isinstance(c.tag, str)]
    for t in other_by_tag.keys():
        if t not in seen_tags:
            tag_order.append(t)

    # unique tags in order
    ordered_tags: list[str] = []
    for t in tag_order:
        if t not in ordered_tags:
            ordered_tags.append(t)

    for tag in ordered_tags:
        b_list = base_by_tag.get(tag, [])
        o_list = other_by_tag.get(tag, [])
        n = max(len(b_list), len(o_list))
        for i in range(n):
            if i < len(b_list) and i < len(o_list):
                _xml_merge_union(b_list[i], o_list[i])
            elif i < len(o_list):
                base.append(deepcopy(o_list[i]))


def _xml_apply_jinja_union(
    role_prefix: str,
    root: ET.Element,
    optional_elements: set[tuple[str, ...]],
) -> str:
    """Generate XML template with optional element conditionals."""
    handler = XmlHandler()

    def wrap_optional_children(elem: ET.Element, path: tuple[str, ...]) -> None:
        children = [c for c in list(elem) if isinstance(c.tag, str)]
        if not children:
            return

        # compute indexed paths the same way as flatten
        counts = Counter(child.tag for child in children)
        index_counters: dict[str, int] = defaultdict(int)
        new_children: list[ET.Element] = []
        for child in children:
            tag = child.tag
            if counts[tag] > 1:
                idx = index_counters[tag]
                index_counters[tag] += 1
                child_path = path + (tag, str(idx))
            else:
                child_path = path + (tag,)

            if child_path in optional_elements:
                cond = defined_var_name(role_prefix, child_path)
                new_children.append(ET.Comment(f"IF:{cond}"))
                new_children.append(child)
                new_children.append(ET.Comment(f"ENDIF:{cond}"))
            else:
                new_children.append(child)

            wrap_optional_children(child, child_path)

        # replace
        for c in children:
            elem.remove(c)
        for c in new_children:
            elem.append(c)

    # Wrap optionals before applying scalar substitution so markers stay
    wrap_optional_children(root, ())
    handler._apply_jinja_to_xml_tree(role_prefix, root, loop_candidates=None)  # type: ignore[attr-defined]

    indent = getattr(ET, "indent", None)
    if indent is not None:
        indent(root, space="  ")  # type: ignore[arg-type]

    xml_body = ET.tostring(root, encoding="unicode")
    # Reuse handler's conditional-marker replacement
    xml_body = handler._insert_xml_loops(xml_body, role_prefix, [], root)  # type: ignore[attr-defined]
    return xml_body


@dataclass
class FormatOutput:
    fmt: str
    template: str
    list_var: str
    items: list[dict[str, Any]]


def process_directory(
    root: Path, recursive: bool, role_prefix: str
) -> tuple[str, list[FormatOutput]]:
    """Process a directory (or single file) into defaults YAML + template(s)."""
    files = iter_supported_files(root, recursive)
    if not files:
        raise ValueError(f"No supported config files found under: {root}")

    # Parse and group by format
    grouped: dict[str, list[tuple[Path, Any]]] = defaultdict(list)
    for p in files:
        fmt, parsed = parse_config(p, None)
        grouped[fmt].append((p, parsed))

    multiple_formats = len(grouped) > 1
    outputs: list[FormatOutput] = []

    for fmt, entries in sorted(grouped.items()):
        rel_ids = [
            e[0].relative_to(root).as_posix() if root.is_dir() else e[0].name
            for e in entries
        ]
        parsed_list = [e[1] for e in entries]

        # JSON: simplest robust union template
        if fmt == "json":
            list_var = (
                f"{role_prefix}_{fmt}_items"
                if multiple_formats
                else f"{role_prefix}_items"
            )
            template = "{{ data | tojson(indent=2) }}\n"
            items: list[dict[str, Any]] = []
            for rid, parsed in zip(rel_ids, parsed_list):
                items.append({"id": rid, "data": parsed})
            outputs.append(
                FormatOutput(fmt=fmt, template=template, list_var=list_var, items=items)
            )
            continue

        # Dict-like formats (YAML/TOML) use union merge on parsed objects
        if fmt in {"yaml", "toml"}:
            union_obj: Any = deepcopy(parsed_list[0])
            for p in parsed_list[1:]:
                union_obj = _merge_union(union_obj, p)

            container_sets: list[set[tuple[str, ...]]] = []
            leaf_sets: list[set[tuple[str, ...]]] = []
            for p in parsed_list:
                containers, leaves = _collect_dict_like_paths(p)
                container_sets.append(containers)
                leaf_sets.append(leaves)

            cont_union = set().union(*container_sets)
            cont_inter = set.intersection(*container_sets) if container_sets else set()
            optional_containers = cont_union - cont_inter

            list_var = (
                f"{role_prefix}_{fmt}_items"
                if multiple_formats
                else f"{role_prefix}_items"
            )

            if fmt == "yaml":
                template_lines = _yaml_render_union(
                    role_prefix, union_obj, optional_containers
                )
                template = "\n".join(template_lines).rstrip() + "\n"
            else:
                if not isinstance(union_obj, dict):
                    raise TypeError("TOML union must be a dict")
                template = _toml_render_union(
                    role_prefix, union_obj, optional_containers
                )

            # Build per-file item dicts (leaf vars + presence markers)
            items: list[dict[str, Any]] = []
            for rid, parsed, containers in zip(rel_ids, parsed_list, container_sets):
                item: dict[str, Any] = {"id": rid}
                flat = flatten_config(fmt, parsed, loop_candidates=None)
                for path, value in flat:
                    item[make_var_name(role_prefix, path)] = value
                for cpath in optional_containers:
                    if cpath in containers:
                        item[defined_var_name(role_prefix, cpath)] = True
                items.append(item)

            outputs.append(
                FormatOutput(fmt=fmt, template=template, list_var=list_var, items=items)
            )
            continue

        if fmt == "ini":
            parsers = parsed_list
            if not all(isinstance(p, configparser.ConfigParser) for p in parsers):
                raise TypeError("INI parse must produce ConfigParser")
            union, opt_sections, opt_keys = _ini_union_and_presence(parsers)  # type: ignore[arg-type]

            list_var = (
                f"{role_prefix}_{fmt}_items"
                if multiple_formats
                else f"{role_prefix}_items"
            )
            template = _ini_render_union(role_prefix, union, opt_sections, opt_keys)

            items: list[dict[str, Any]] = []
            for rid, parser in zip(rel_ids, parsers):  # type: ignore[arg-type]
                item: dict[str, Any] = {"id": rid}
                flat = flatten_config(fmt, parser, loop_candidates=None)
                for path, value in flat:
                    item[make_var_name(role_prefix, path)] = value
                # section presence
                for sec in opt_sections:
                    if parser.has_section(sec):
                        item[defined_var_name(role_prefix, (sec,))] = True
                # key presence
                for sec, key in opt_keys:
                    if parser.has_option(sec, key):
                        item[defined_var_name(role_prefix, (sec, key))] = True
                items.append(item)

            outputs.append(
                FormatOutput(fmt=fmt, template=template, list_var=list_var, items=items)
            )
            continue

        if fmt == "xml":
            if not all(isinstance(p, ET.Element) for p in parsed_list):
                raise TypeError("XML parse must produce Element")
            union_root = deepcopy(parsed_list[0])
            for p in parsed_list[1:]:
                _xml_merge_union(union_root, p)

            elem_sets: list[set[tuple[str, ...]]] = []
            for p in parsed_list:
                elem_paths, _ = _xml_collect_paths(p)
                elem_sets.append(elem_paths)

            elem_union = set().union(*elem_sets)
            elem_inter = set.intersection(*elem_sets) if elem_sets else set()
            optional_elements = (elem_union - elem_inter) - {()}  # never wrap root

            list_var = (
                f"{role_prefix}_{fmt}_items"
                if multiple_formats
                else f"{role_prefix}_items"
            )
            template = _xml_apply_jinja_union(
                role_prefix, union_root, optional_elements
            )

            items: list[dict[str, Any]] = []
            for rid, parsed, elems in zip(rel_ids, parsed_list, elem_sets):
                item: dict[str, Any] = {"id": rid}
                flat = flatten_config(fmt, parsed, loop_candidates=None)
                for path, value in flat:
                    item[make_var_name(role_prefix, path)] = value
                for epath in optional_elements:
                    if epath in elems:
                        item[defined_var_name(role_prefix, epath)] = True
                items.append(item)

            outputs.append(
                FormatOutput(fmt=fmt, template=template, list_var=list_var, items=items)
            )
            continue

        raise ValueError(f"Unsupported format in folder mode: {fmt}")

    # Build combined defaults YAML
    defaults_doc: dict[str, Any] = {}
    for out in outputs:
        defaults_doc[out.list_var] = out.items
    defaults_yaml = dump_yaml(defaults_doc, sort_keys=True)

    return defaults_yaml, outputs
