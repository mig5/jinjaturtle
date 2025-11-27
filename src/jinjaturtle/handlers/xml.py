from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET  # nosec

from . import BaseHandler


class XmlHandler(BaseHandler):
    fmt = "xml"

    def parse(self, path: Path) -> ET.Element:
        text = path.read_text(encoding="utf-8")
        # Parse with an explicit XMLParser instance so this stays compatible
        # with Python versions where xml.etree.ElementTree.fromstring() may
        # not accept a ``parser=`` keyword argument.
        # defusedxml.defuse_stdlib() is called in the CLI entrypoint, so using
        # the stdlib XMLParser here is safe.
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
        if original_text is not None:
            return self._generate_xml_template_from_text(role_prefix, original_text)
        if not isinstance(parsed, ET.Element):
            raise TypeError("XML parser result must be an Element")
        xml_str = ET.tostring(parsed, encoding="unicode")
        return self._generate_xml_template_from_text(role_prefix, xml_str)

    def _flatten_xml(self, root: ET.Element) -> list[tuple[tuple[str, ...], Any]]:
        """
        Flatten an XML tree into (path, value) pairs.

        Path conventions:
          - Root element's children are treated as top-level (root tag is *not* included).
          - Element text:
              <foo>bar</foo>           -> path ("foo",)          value "bar"
              <foo attr="x">bar</foo>  -> path ("foo", "value")  value "bar"
              <foo><bar>baz</bar></foo> -> ("foo", "bar") / etc.
          - Attributes:
              <server host="localhost">
                -> path ("server", "@host") value "localhost"
          - Repeated sibling elements:
              <endpoint>/a</endpoint>
              <endpoint>/b</endpoint>
                -> ("endpoint", "0") "/a"
                   ("endpoint", "1") "/b"
        """
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
                    # Simple <foo>bar</foo>
                    items.append((path, text))
                else:
                    # Text alongside attrs/children
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

        # Treat root as a container: its children are top-level
        walk(root, ())
        return items

    def _split_xml_prolog(self, text: str) -> tuple[str, str]:
        """
        Split an XML document into (prolog, body), where prolog includes:
          - XML declaration (<?xml ...?>)
          - top-level comments
          - DOCTYPE
        The body starts at the root element.
        """
        i = 0
        n = len(text)
        prolog_parts: list[str] = []

        while i < n:
            # Preserve leading whitespace
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
                # Assume root element starts here
                break

            # Unexpected content: stop treating as prolog
            break

        return "".join(prolog_parts), text[i:]

    def _apply_jinja_to_xml_tree(self, role_prefix: str, root: ET.Element) -> None:
        """
        Mutate the XML tree in-place, replacing scalar values with Jinja
        expressions based on the same paths used in _flatten_xml.
        """

        def walk(elem: ET.Element, path: tuple[str, ...]) -> None:
            # Attributes
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

            # Repeated children get indexes just like in _flatten_xml
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

    def _generate_xml_template_from_text(self, role_prefix: str, text: str) -> str:
        """
        Generate a Jinja2 template for an XML file, preserving comments and prolog.

        - Attributes become Jinja placeholders:
            <server host="localhost" />
              -> <server host="{{ prefix_server_host }}" />

        - Text nodes become placeholders:
            <port>8080</port>
              -> <port>{{ prefix_port }}</port>

          but if the element also has attributes/children, the value path
          gets a trailing "value" component, matching flattening.
        """
        prolog, body = self._split_xml_prolog(text)

        # Parse with comments included so <!-- --> are preserved
        # defusedxml.defuse_stdlib() is called in CLI entrypoint
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))  # nosec B314
        parser.feed(body)
        root = parser.close()

        self._apply_jinja_to_xml_tree(role_prefix, root)

        # Pretty indentation if available (Python 3.9+)
        indent = getattr(ET, "indent", None)
        if indent is not None:
            indent(root, space="  ")  # type: ignore[arg-type]

        xml_body = ET.tostring(root, encoding="unicode")
        return prolog + xml_body
