from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET  # nosec

from .base import BaseHandler
from ..loop_analyzer import LoopCandidate


class XmlHandlerLoopable(BaseHandler):
    """
    XML handler that can generate both scalar templates and loop-based templates.
    """

    fmt = "xml"

    def parse(self, path: Path) -> ET.Element:
        text = path.read_text(encoding="utf-8")
        parser = ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=False)
        )  # nosec B314
        parser.feed(text)
        root = parser.close()
        return root

    def flatten(self, parsed: Any) -> list[tuple[tuple[str, ...], Any]]:
        if not isinstance(parsed, ET.Element):
            raise TypeError("XML parser result must be an Element")
        return self._flatten_xml(parsed)

    def generate_template(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None = None,
    ) -> str:
        """Original scalar-only template generation."""
        if original_text is not None:
            return self._generate_xml_template_from_text(role_prefix, original_text)
        if not isinstance(parsed, ET.Element):
            raise TypeError("XML parser result must be an Element")
        xml_str = ET.tostring(parsed, encoding="unicode")
        return self._generate_xml_template_from_text(role_prefix, xml_str)

    def generate_template_with_loops(
        self,
        parsed: Any,
        role_prefix: str,
        original_text: str | None,
        loop_candidates: list[LoopCandidate],
    ) -> str:
        """Generate template with Jinja2 for loops where appropriate."""

        if original_text is not None:
            return self._generate_xml_template_with_loops_from_text(
                role_prefix, original_text, loop_candidates
            )

        if not isinstance(parsed, ET.Element):
            raise TypeError("XML parser result must be an Element")

        xml_str = ET.tostring(parsed, encoding="unicode")
        return self._generate_xml_template_with_loops_from_text(
            role_prefix, xml_str, loop_candidates
        )

    def _flatten_xml(self, root: ET.Element) -> list[tuple[tuple[str, ...], Any]]:
        """Flatten an XML tree into (path, value) pairs."""
        items: list[tuple[tuple[str, ...], Any]] = []

        def walk(elem: ET.Element, path: tuple[str, ...]) -> None:
            # Attributes
            for attr_name, attr_val in elem.attrib.items():
                attr_path = path + (f"@{attr_name}",)
                items.append((attr_path, attr_val))

            # Children
            children = [c for c in list(elem) if isinstance(c.tag, str)]

            # Text content
            text = (elem.text or "").strip()
            if text:
                if not elem.attrib and not children:
                    items.append((path, text))
                else:
                    items.append((path + ("value",), text))

            # Repeated siblings get an index; singletons just use the tag
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
        return items

    def _split_xml_prolog(self, text: str) -> tuple[str, str]:
        """Split XML into (prolog, body)."""
        i = 0
        n = len(text)
        prolog_parts: list[str] = []

        while i < n:
            while i < n and text[i].isspace():
                prolog_parts.append(text[i])
                i += 1
            if i >= n:
                break

            if text.startswith("<?", i):
                end = text.find("?>", i + 2)
                if end == -1:
                    break
                prolog_parts.append(text[i : end + 2])
                i = end + 2
                continue

            if text.startswith("<!--", i):
                end = text.find("-->", i + 4)
                if end == -1:
                    break
                prolog_parts.append(text[i : end + 3])
                i = end + 3
                continue

            if text.startswith("<!DOCTYPE", i):
                end = text.find(">", i + 9)
                if end == -1:
                    break
                prolog_parts.append(text[i : end + 1])
                i = end + 1
                continue

            if text[i] == "<":
                break

            break

        return "".join(prolog_parts), text[i:]

    def _apply_jinja_to_xml_tree(
        self,
        role_prefix: str,
        root: ET.Element,
        loop_candidates: list[LoopCandidate] | None = None,
    ) -> None:
        """
        Mutate XML tree in-place, replacing values with Jinja expressions.

        If loop_candidates is provided, repeated elements matching a candidate
        will be replaced with a {% for %} loop.
        """

        # Build a map of loop paths for quick lookup
        loop_paths = {}
        if loop_candidates:
            for candidate in loop_candidates:
                loop_paths[candidate.path] = candidate

        def walk(elem: ET.Element, path: tuple[str, ...]) -> None:
            # Attributes (unless this element is in a loop)
            for attr_name in list(elem.attrib.keys()):
                attr_path = path + (f"@{attr_name}",)
                var_name = self.make_var_name(role_prefix, attr_path)
                elem.set(attr_name, f"{{{{ {var_name} }}}}")

            # Children
            children = [c for c in list(elem) if isinstance(c.tag, str)]

            # Text content
            text = (elem.text or "").strip()
            if text:
                if not elem.attrib and not children:
                    text_path = path
                else:
                    text_path = path + ("value",)
                var_name = self.make_var_name(role_prefix, text_path)
                elem.text = f"{{{{ {var_name} }}}}"

            # Handle children - check for loops first
            counts = Counter(child.tag for child in children)
            index_counters: dict[str, int] = defaultdict(int)

            # Check each tag to see if it's a loop candidate
            processed_tags = set()

            for child in children:
                tag = child.tag

                # Skip if we've already processed this tag as a loop
                if tag in processed_tags:
                    continue

                child_path = path + (tag,)

                # Check if this is a loop candidate
                if child_path in loop_paths:
                    # Mark this tag as processed
                    processed_tags.add(tag)

                    # Remove all children with this tag
                    for child_to_remove in [c for c in children if c.tag == tag]:
                        elem.remove(child_to_remove)

                    # Create a loop comment/marker
                    # We'll handle the actual loop generation in text processing
                    loop_marker = ET.Comment(f"LOOP:{tag}")
                    elem.append(loop_marker)

                elif counts[tag] > 1:
                    # Multiple children but not a loop candidate - use indexed paths
                    idx = index_counters[tag]
                    index_counters[tag] += 1
                    indexed_path = path + (tag, str(idx))
                    walk(child, indexed_path)
                else:
                    # Single child
                    walk(child, child_path)

        walk(root, ())

    def _generate_xml_template_from_text(self, role_prefix: str, text: str) -> str:
        """Generate scalar-only Jinja2 template."""
        prolog, body = self._split_xml_prolog(text)

        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))  # nosec B314
        parser.feed(body)
        root = parser.close()

        self._apply_jinja_to_xml_tree(role_prefix, root)

        indent = getattr(ET, "indent", None)
        if indent is not None:
            indent(root, space="  ")  # type: ignore[arg-type]

        xml_body = ET.tostring(root, encoding="unicode")
        return prolog + xml_body

    def _generate_xml_template_with_loops_from_text(
        self,
        role_prefix: str,
        text: str,
        loop_candidates: list[LoopCandidate],
    ) -> str:
        """Generate Jinja2 template with for loops."""

        prolog, body = self._split_xml_prolog(text)

        # Parse with comments preserved
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))  # nosec B314
        parser.feed(body)
        root = parser.close()

        # Apply Jinja transformations (including loop markers)
        self._apply_jinja_to_xml_tree(role_prefix, root, loop_candidates)

        # Convert to string
        indent = getattr(ET, "indent", None)
        if indent is not None:
            indent(root, space="  ")  # type: ignore[arg-type]

        xml_body = ET.tostring(root, encoding="unicode")

        # Post-process to replace loop markers with actual Jinja loops
        xml_body = self._insert_xml_loops(xml_body, role_prefix, loop_candidates, root)

        return prolog + xml_body

    def _insert_xml_loops(
        self,
        xml_str: str,
        role_prefix: str,
        loop_candidates: list[LoopCandidate],
        root: ET.Element,
    ) -> str:
        """
        Post-process XML string to insert Jinja2 for loops.

        This replaces <!--LOOP:tagname--> markers with actual loop constructs.
        """

        # Build a sample element for each loop to use as template
        lines = xml_str.split("\n")
        result_lines = []

        for line in lines:
            # Check if this line contains a loop marker
            if "<!--LOOP:" in line:
                # Extract tag name from marker
                start = line.find("<!--LOOP:") + 9
                end = line.find("-->", start)
                tag_name = line[start:end].strip()

                # Find matching loop candidate
                candidate = None
                for cand in loop_candidates:
                    if cand.path and cand.path[-1] == tag_name:
                        candidate = cand
                        break

                if candidate:
                    # Get indentation from current line
                    indent_level = len(line) - len(line.lstrip())
                    indent_str = " " * indent_level

                    # Generate loop variable name
                    collection_var = self.make_var_name(role_prefix, candidate.path)
                    item_var = candidate.loop_var

                    # Create sample element from first item
                    if candidate.items:
                        sample_elem = self._dict_to_xml_element(
                            tag_name, candidate.items[0], item_var
                        )

                        # Apply indentation to the sample element
                        ET.indent(sample_elem, space="  ")

                        # Convert sample to string
                        sample_str = ET.tostring(
                            sample_elem, encoding="unicode"
                        ).strip()

                        # Add proper indentation to each line of the sample
                        sample_lines = sample_str.split("\n")
                        indented_sample_lines = [
                            (
                                f"{indent_str}  {line}"
                                if i > 0
                                else f"{indent_str}  {line}"
                            )
                            for i, line in enumerate(sample_lines)
                        ]
                        indented_sample = "\n".join(indented_sample_lines)

                        # Build loop
                        result_lines.append(
                            f"{indent_str}{{% for {item_var} in {collection_var} %}}"
                        )
                        result_lines.append(indented_sample)
                        result_lines.append(f"{indent_str}{{% endfor %}}")
                else:
                    # Keep the marker if we can't find the candidate
                    result_lines.append(line)
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    def _dict_to_xml_element(
        self, tag: str, data: dict[str, Any], loop_var: str
    ) -> ET.Element:
        """
        Convert a dict to an XML element with Jinja2 variable references.

        Args:
            tag: Element tag name
            data: Dict representing element structure
            loop_var: Loop variable name to use in Jinja expressions
        """

        elem = ET.Element(tag)

        # Handle attributes and child elements
        for key, value in data.items():
            if key.startswith("@"):
                # Attribute
                attr_name = key[1:]  # Remove @ prefix
                elem.set(attr_name, f"{{{{ {loop_var}.{attr_name} }}}}")
            elif key == "_text":
                # Simple text content
                elem.text = f"{{{{ {loop_var} }}}}"
            elif key == "value":
                # Text with attributes/children
                elem.text = f"{{{{ {loop_var}.value }}}}"
            elif key == "_key":
                # This is the dict key (for dict collections), skip in XML
                pass
            elif isinstance(value, dict):
                # Nested element - check if it has _text
                child = ET.SubElement(elem, key)
                if "_text" in value:
                    child.text = f"{{{{ {loop_var}.{key}._text }}}}"
                else:
                    # More complex nested structure
                    for sub_key, sub_val in value.items():
                        if not sub_key.startswith("_"):
                            grandchild = ET.SubElement(child, sub_key)
                            grandchild.text = f"{{{{ {loop_var}.{key}.{sub_key} }}}}"
            elif not isinstance(value, list):
                # Simple child element (scalar value)
                child = ET.SubElement(elem, key)
                child.text = f"{{{{ {loop_var}.{key} }}}}"

        return elem
