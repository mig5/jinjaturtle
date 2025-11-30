"""
Loop detection and analysis for intelligent Jinja2 template generation.

This module determines when config structures should use Jinja2 'for' loops
instead of flattened scalar variables.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal


class LoopCandidate:
    """
    Represents a detected loop opportunity in the config structure.

    Attributes:
        path: Path to the collection (e.g. ("servers",) or ("config", "endpoints"))
        loop_var: Variable name for loop items (e.g. "server", "endpoint")
        items: The actual list/dict items that will be looped over
        item_schema: Structure of each item ("scalar", "simple_dict", "nested")
        confidence: How confident we are this should be a loop (0.0 to 1.0)
    """

    def __init__(
        self,
        path: tuple[str, ...],
        loop_var: str,
        items: list[Any] | dict[str, Any],
        item_schema: Literal["scalar", "simple_dict", "nested"],
        confidence: float = 1.0,
    ):
        self.path = path
        self.loop_var = loop_var
        self.items = items
        self.item_schema = item_schema
        self.confidence = confidence

    def __repr__(self) -> str:
        path_str = ".".join(self.path) if self.path else "<root>"
        return (
            f"LoopCandidate(path={path_str}, var={self.loop_var}, "
            f"count={len(self.items)}, schema={self.item_schema}, "
            f"confidence={self.confidence:.2f})"
        )


class LoopAnalyzer:
    """
    Analyzes parsed config structures to detect loop opportunities.

    Strategy:
    1. Detect homogeneous lists (all items same type/structure)
    2. Detect dict collections where all values have similar structure
    3. Assign confidence scores based on:
       - Homogeneity of items
       - Number of items (2+ for loops to make sense)
       - Depth and complexity (too nested -> fallback to scalars)
       - Structural patterns (e.g., repeated XML elements)
    """

    # Configuration thresholds
    MIN_ITEMS_FOR_LOOP = 2  # Need at least 2 items to justify a loop
    MAX_NESTING_DEPTH = 3  # Beyond this, use scalar fallback
    MIN_CONFIDENCE = 0.7  # Minimum confidence to use a loop

    def __init__(self):
        self.candidates: list[LoopCandidate] = []

    def analyze(self, parsed: Any, fmt: str) -> list[LoopCandidate]:
        """
        Analyze a parsed config structure and return loop candidates.

        Args:
            parsed: The parsed config (dict, list, or ET.Element for XML)
            fmt: Format type ("yaml", "json", "toml", "xml", "ini")

        Returns:
            List of LoopCandidate objects, sorted by path depth (shallowest first)
        """
        self.candidates = []

        if fmt == "xml":
            self._analyze_xml(parsed)
        elif fmt in ("yaml", "json", "toml"):
            self._analyze_dict_like(parsed, path=())
        elif fmt == "ini":
            # INI files are typically flat key-value, not suitable for loops
            pass

        # Sort by path depth (process parent structures before children)
        self.candidates.sort(key=lambda c: len(c.path))
        return self.candidates

    def _analyze_dict_like(
        self,
        obj: Any,
        path: tuple[str, ...],
        depth: int = 0,
        parent_is_list: bool = False,
    ) -> None:
        """Recursively analyze dict/list structures."""

        # Safety: don't go too deep
        if depth > self.MAX_NESTING_DEPTH:
            return

        if isinstance(obj, dict):
            # Check if this dict's values form a homogeneous collection
            if len(obj) >= self.MIN_ITEMS_FOR_LOOP:
                candidate = self._check_dict_collection(obj, path)
                if candidate:
                    self.candidates.append(candidate)
                    # Don't recurse into items we've marked as a loop
                    return

            # Recurse into dict values
            for key, value in obj.items():
                self._analyze_dict_like(
                    value, path + (str(key),), depth + 1, parent_is_list=False
                )

        elif isinstance(obj, list):
            # Don't create loop candidates for nested lists (lists inside lists)
            # These are too complex for clean template generation and should fall back to scalar handling
            if parent_is_list:
                return

            # Check if this list is homogeneous
            if len(obj) >= self.MIN_ITEMS_FOR_LOOP:
                candidate = self._check_list_collection(obj, path)
                if candidate:
                    self.candidates.append(candidate)
                    # Don't recurse into items we've marked as a loop
                    return

            # If not a good loop candidate, recurse into items
            # Pass parent_is_list=True so nested lists won't create loop candidates
            for i, item in enumerate(obj):
                self._analyze_dict_like(
                    item, path + (str(i),), depth + 1, parent_is_list=True
                )

    def _check_list_collection(
        self, items: list[Any], path: tuple[str, ...]
    ) -> LoopCandidate | None:
        """Check if a list should be a loop."""

        if not items:
            return None

        # Analyze item types and structures
        item_types = [type(item).__name__ for item in items]
        type_counts = Counter(item_types)

        # Must be homogeneous (all same type)
        if len(type_counts) != 1:
            return None

        item_type = item_types[0]

        # Scalar list (strings, numbers, bools)
        if item_type in ("str", "int", "float", "bool", "NoneType"):
            return LoopCandidate(
                path=path,
                loop_var=self._derive_loop_var(path, singular=True),
                items=items,
                item_schema="scalar",
                confidence=1.0,
            )

        # List of dicts - check structural homogeneity
        if item_type == "dict":
            schema = self._analyze_dict_schema(items)
            if schema == "simple_dict":
                return LoopCandidate(
                    path=path,
                    loop_var=self._derive_loop_var(path, singular=True),
                    items=items,
                    item_schema="simple_dict",
                    confidence=0.95,
                )
            elif schema == "homogeneous":
                return LoopCandidate(
                    path=path,
                    loop_var=self._derive_loop_var(path, singular=True),
                    items=items,
                    item_schema="simple_dict",
                    confidence=0.85,
                )
            # If too complex/heterogeneous, return None (use scalar fallback)

        return None

    def _check_dict_collection(
        self, obj: dict[str, Any], path: tuple[str, ...]
    ) -> LoopCandidate | None:
        """
        Check if a dict's values form a collection suitable for looping.

        Example: {"server1": {...}, "server2": {...}} where all values
        have the same structure.

        NOTE: Currently disabled for TOML compatibility. TOML's dict-of-tables
        syntax ([servers.alpha], [servers.beta]) cannot be easily converted to
        loops without restructuring the entire TOML format. To maintain consistency
        between Ansible YAML and Jinja2 templates, we treat these as scalars.
        """

        # TODO: Re-enable this if we implement proper dict-of-tables loop generation
        # For now, return None to use scalar handling
        return None

        # Original logic preserved below for reference:
        # if not obj:
        #     return None
        #
        # values = list(obj.values())
        #
        # # Check type homogeneity
        # value_types = [type(v).__name__ for v in values]
        # type_counts = Counter(value_types)
        #
        # if len(type_counts) != 1:
        #     return None
        #
        # value_type = value_types[0]
        #
        # # Only interested in dict values for dict collections
        # # (scalar-valued dicts stay as scalars)
        # if value_type != "dict":
        #     return None
        #
        # # Check structural homogeneity
        # schema = self._analyze_dict_schema(values)
        # if schema in ("simple_dict", "homogeneous"):
        #     confidence = 0.9 if schema == "simple_dict" else 0.8
        #
        #     # Convert dict to list of items with 'key' added
        #     items_with_keys = [{"_key": k, **v} for k, v in obj.items()]
        #
        #     return LoopCandidate(
        #         path=path,
        #         loop_var=self._derive_loop_var(path, singular=True),
        #         items=items_with_keys,
        #         item_schema="simple_dict",
        #         confidence=confidence,
        #     )
        #
        # return None

    def _analyze_dict_schema(
        self, dicts: list[dict[str, Any]]
    ) -> Literal["simple_dict", "homogeneous", "heterogeneous"]:
        """
        Analyze a list of dicts to determine their structural homogeneity.

        Returns:
            "simple_dict": All dicts have same keys, all values are scalars
            "homogeneous": All dicts have same keys, may have nested structures
            "heterogeneous": Dicts have different structures
        """

        if not dicts:
            return "heterogeneous"

        # Get key sets from each dict
        key_sets = [set(d.keys()) for d in dicts]

        # Check if all have the same keys
        first_keys = key_sets[0]
        if not all(ks == first_keys for ks in key_sets):
            # Allow minor variations (80% key overlap)
            all_keys = set().union(*key_sets)
            common_keys = set.intersection(*key_sets)
            if len(common_keys) / len(all_keys) < 0.8:
                return "heterogeneous"

        # Check if values are all scalars
        all_scalars = True
        for d in dicts:
            for v in d.values():
                if isinstance(v, (dict, list)):
                    all_scalars = False
                    break
            if not all_scalars:
                break

        if all_scalars:
            return "simple_dict"
        else:
            return "homogeneous"

    def _derive_loop_var(self, path: tuple[str, ...], singular: bool = True) -> str:
        """
        Derive a sensible loop variable name from the path.

        Examples:
            ("servers",) -> "server" (singular)
            ("config", "endpoints") -> "endpoint"
            ("users",) -> "user"
            ("databases",) -> "database"
        """

        if not path:
            return "item"

        last_part = path[-1].lower()

        if singular:
            # Simple English pluralization rules (order matters - most specific first)
            if last_part.endswith("sses"):
                return last_part[:-2]  # "classes" -> "class"
            elif last_part.endswith("xes"):
                return last_part[:-2]  # "boxes" -> "box"
            elif last_part.endswith("ches"):
                return last_part[:-2]  # "watches" -> "watch"
            elif last_part.endswith("shes"):
                return last_part[:-2]  # "dishes" -> "dish"
            elif last_part.endswith("ies"):
                return last_part[:-3] + "y"  # "entries" -> "entry"
            elif last_part.endswith("oes"):
                return last_part[:-2]  # "tomatoes" -> "tomato"
            elif last_part.endswith("ses") and not last_part.endswith("sses"):
                # Only for words ending in "se": "databases" -> "database"
                # But NOT for "sses" which we already handled
                if len(last_part) > 3 and last_part[-4] not in "aeiou":
                    # "databases" -> "database" (consonant before 's')
                    return last_part[:-1]
                else:
                    # "houses" -> "house", "causes" -> "cause"
                    return last_part[:-1]
            elif last_part.endswith("s") and not last_part.endswith("ss"):
                return last_part[:-1]  # "servers" -> "server"

        return last_part

    def _analyze_xml(self, root: Any) -> None:
        """
        Analyze XML structure for loop opportunities.

        XML is particularly suited for loops when we have repeated sibling elements.
        """
        import xml.etree.ElementTree as ET  # nosec B405

        if not isinstance(root, ET.Element):
            return

        self._walk_xml_element(root, path=())

    def _walk_xml_element(self, elem: Any, path: tuple[str, ...]) -> None:
        """Recursively walk XML elements looking for repeated siblings."""

        children = [c for c in list(elem) if isinstance(c.tag, str)]

        # Count sibling elements by tag
        tag_counts = Counter(child.tag for child in children)

        # Find repeated tags
        for tag, count in tag_counts.items():
            if count >= self.MIN_ITEMS_FOR_LOOP:
                # Get all elements with this tag
                tagged_elements = [c for c in children if c.tag == tag]

                # Check homogeneity
                if self._are_xml_elements_homogeneous(tagged_elements):
                    # Convert to dict representation for easier handling
                    items = [self._xml_elem_to_dict(el) for el in tagged_elements]

                    # Determine schema
                    if all(self._is_scalar_dict(item) for item in items):
                        schema = "simple_dict"
                        confidence = 1.0
                    else:
                        schema = "nested"
                        confidence = 0.8

                    candidate = LoopCandidate(
                        path=path + (tag,),
                        loop_var=self._derive_loop_var((tag,), singular=True),
                        items=items,
                        item_schema=schema,
                        confidence=confidence,
                    )
                    self.candidates.append(candidate)

        # Recurse into unique children (non-repeated ones will be processed normally)
        for tag, count in tag_counts.items():
            if count == 1:
                child = next(c for c in children if c.tag == tag)
                self._walk_xml_element(child, path + (tag,))

    def _are_xml_elements_homogeneous(self, elements: list[Any]) -> bool:
        """Check if XML elements have similar structure."""

        if not elements:
            return False

        # Compare attribute sets
        attr_sets = [set(el.attrib.keys()) for el in elements]
        first_attrs = attr_sets[0]

        if not all(attrs == first_attrs for attrs in attr_sets):
            # Allow some variation
            all_attrs = set().union(*attr_sets)
            common_attrs = set.intersection(*attr_sets) if attr_sets else set()
            # Very permissive for attributes - 20% overlap is OK
            if len(common_attrs) / max(len(all_attrs), 1) < 0.2:
                return False

        # Compare child element tags
        child_tag_sets = [
            set(c.tag for c in el if hasattr(c, "tag")) for el in elements
        ]

        if child_tag_sets:
            first_tags = child_tag_sets[0]
            if not all(tags == first_tags for tags in child_tag_sets):
                # Allow significant variation for XML - just need SOME commonality
                # This is important for cases like OSSEC rules where each rule
                # has different optional child elements (if_sid, url_pcre2, etc.)
                all_tags = set().union(*child_tag_sets)
                common_tags = (
                    set.intersection(*child_tag_sets) if child_tag_sets else set()
                )
                # Lower threshold to 20% - if they share at least 20% of tags, consider them similar
                # Even if they just share 'description' or 'id' fields, that's enough
                if len(common_tags) / max(len(all_tags), 1) < 0.2:
                    return False

        return True

    def _xml_elem_to_dict(self, elem: Any) -> dict[str, Any]:
        """Convert an XML element to a dict representation."""
        result: dict[str, Any] = {}

        # Add attributes
        for attr_name, attr_val in elem.attrib.items():
            result[f"@{attr_name}"] = attr_val

        # Add text content
        text = (elem.text or "").strip()
        if text:
            children = [c for c in list(elem) if hasattr(c, "tag")]
            if not elem.attrib and not children:
                result["_text"] = text
            else:
                result["value"] = text

        # Add child elements
        for child in elem:
            if hasattr(child, "tag"):
                child_dict = self._xml_elem_to_dict(child)
                if child.tag in result:
                    # Multiple children with same tag - convert to list
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child_dict)
                else:
                    result[child.tag] = child_dict

        return result

    def _is_scalar_dict(self, obj: dict[str, Any]) -> bool:
        """Check if a dict contains only scalar values (no nested dicts/lists)."""
        for v in obj.values():
            if isinstance(v, (dict, list)):
                return False
        return True
