from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import datetime
import re
import yaml

from .loop_analyzer import LoopAnalyzer, LoopCandidate
from .safety import (
    verify_jinja2_template_safe,
    verify_no_live_jinja_in_json_keys,
)
from .handlers import (
    BaseHandler,
    IniHandler,
    JsonHandler,
    TomlHandler,
    YamlHandler,
    XmlHandler,
    PostfixMainHandler,
    SystemdUnitHandler,
    SshConfigHandler,
)


class QuotedString(str):
    """
    Marker type for strings that must be double-quoted in YAML output.
    """

    pass


class AnsibleUnsafeString(str):
    """Marker type emitted with Ansible's !unsafe YAML tag.

    Ansible recursively templates string values by default.  Source-derived
    config values that contain Jinja delimiters must therefore be marked
    unsafe in defaults/main.yml, otherwise a harvested value such as
    ``{{ lookup('pipe', 'id') }}`` becomes executable on the Ansible
    controller when the generated role is applied.
    """

    pass


_JINJA_STARTS = ("{{", "{%", "{#")


def _fallback_str_representer(dumper: yaml.SafeDumper, data: Any):
    """
    Fallback for objects the dumper doesn't know about.
    """
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data))


class _TurtleDumper(yaml.SafeDumper):
    """
    Custom YAML dumper that always double-quotes QuotedString values.
    """

    pass


def _quoted_str_representer(dumper: yaml.SafeDumper, data: QuotedString):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


def _ansible_unsafe_str_representer(dumper: yaml.SafeDumper, data: AnsibleUnsafeString):
    return dumper.represent_scalar("!unsafe", str(data), style="'")


def _needs_ansible_unsafe(value: str) -> bool:
    return any(marker in value for marker in _JINJA_STARTS)


def _mark_ansible_unsafe_values(obj: Any) -> Any:
    """Recursively mark mapping/list values containing Jinja as !unsafe.

    Mapping keys are intentionally left alone: they are variable names or YAML
    structure, not Ansible-templated values.  Values nested in folder-mode item
    lists, including source-derived ``id`` values, are protected.
    """

    if isinstance(obj, dict):
        return {k: _mark_ansible_unsafe_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mark_ansible_unsafe_values(v) for v in obj]
    if isinstance(obj, str) and _needs_ansible_unsafe(obj):
        return AnsibleUnsafeString(obj)
    return obj


_TurtleDumper.add_representer(QuotedString, _quoted_str_representer)
_TurtleDumper.add_representer(AnsibleUnsafeString, _ansible_unsafe_str_representer)
# Use our fallback for any unknown object types
_TurtleDumper.add_representer(None, _fallback_str_representer)

_HANDLERS: dict[str, BaseHandler] = {}

_INI_HANDLER = IniHandler()
_JSON_HANDLER = JsonHandler()
_TOML_HANDLER = TomlHandler()
_YAML_HANDLER = YamlHandler()
_XML_HANDLER = XmlHandler()

_POSTFIX_HANDLER = PostfixMainHandler()
_SYSTEMD_HANDLER = SystemdUnitHandler()
_SSH_HANDLER = SshConfigHandler()

_HANDLERS["ini"] = _INI_HANDLER
_HANDLERS["json"] = _JSON_HANDLER
_HANDLERS["toml"] = _TOML_HANDLER
_HANDLERS["yaml"] = _YAML_HANDLER
_HANDLERS["xml"] = _XML_HANDLER

_HANDLERS["postfix"] = _POSTFIX_HANDLER
_HANDLERS["systemd"] = _SYSTEMD_HANDLER
_HANDLERS["ssh"] = _SSH_HANDLER


def dump_yaml(data: Any, *, sort_keys: bool = True) -> str:
    """Dump YAML using JinjaTurtle's dumper settings.

    This is used by both the single-file and multi-file code paths.
    """
    safe_data = _mark_ansible_unsafe_values(data)
    return yaml.dump(
        safe_data,
        Dumper=_TurtleDumper,
        sort_keys=sort_keys,
        default_flow_style=False,
        allow_unicode=True,
        explicit_start=True,
        indent=2,
    )


def make_var_name(role_prefix: str, path: Iterable[str]) -> str:
    """
    Wrapper for :meth:`BaseHandler.make_var_name`.
    """
    return BaseHandler.make_var_name(role_prefix, path)


def _read_head(path: Path, max_bytes: int = 65536) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except OSError:
        return ""


_SYSTEMD_SUFFIXES: set[str] = {
    ".service",
    ".socket",
    ".target",
    ".timer",
    ".path",
    ".mount",
    ".automount",
    ".slice",
    ".swap",
    ".scope",
    ".link",
    ".netdev",
    ".network",
}


def _looks_like_systemd(text: str) -> bool:
    # Be conservative: many INI-style configs have [section] and key=value.
    # systemd unit files almost always contain one of these well-known sections.
    if re.search(
        r"^\s*\[(Unit|Service|Install|Socket|Timer|Path|Mount|Automount|Slice|Swap|Scope)\]\s*$",
        text,
        re.M,
    ) and re.search(r"^\s*\w[\w\-]*\s*=", text, re.M):
        return True
    return False


def _looks_like_ssh_config(text: str) -> bool:
    """Conservatively sniff OpenSSH config snippets.

    This is intentionally stricter than generic key/value detection so random
    .conf files are not misclassified. Exact ssh_config/sshd_config filenames
    are handled separately above.
    """
    meaningful: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        meaningful.append(stripped)
        if len(meaningful) >= 20:
            break

    if not meaningful:
        return False

    ssh_keywords = {
        "acceptenv",
        "addressfamily",
        "allowgroups",
        "allowtcpforwarding",
        "allowusers",
        "authenticationmethods",
        "authorizedkeysfile",
        "banner",
        "ciphers",
        "chrootdirectory",
        "denyusers",
        "forcecommand",
        "forwardagent",
        "host",
        "hostbasedauthentication",
        "hostkey",
        "hostname",
        "identityfile",
        "include",
        "kexalgorithms",
        "listenaddress",
        "loglevel",
        "match",
        "passwordauthentication",
        "permitrootlogin",
        "port",
        "proxycommand",
        "proxyjump",
        "pubkeyauthentication",
        "sendenv",
        "subsystem",
        "user",
        "x11forwarding",
    }

    hits = 0
    for line in meaningful:
        m = re.match(r"^([^\s=#]+)", line)
        if not m:
            continue
        if m.group(1).lower() in ssh_keywords:
            hits += 1

    return hits >= 2 or (len(meaningful) <= 3 and hits >= 1)


def detect_format(path: Path, explicit: str | None = None) -> str:
    """
    Determine config format.

    For unambiguous extensions (json/yaml/toml/xml/ini), we rely on the suffix.
    For ambiguous extensions like '.conf' (or no extension), we sniff the content.
    """
    if explicit:
        return explicit

    suffix = path.suffix.lower()
    name = path.name.lower()

    # Unambiguous extensions
    if suffix == ".toml":
        return "toml"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix == ".xml":
        return "xml"

    # Special-ish INI-like formats
    if suffix in {".ini", ".cfg"} or name.endswith(".ini"):
        return "ini"
    if suffix == ".repo":
        return "ini"

    # systemd units
    if suffix in _SYSTEMD_SUFFIXES:
        return "systemd"

    # well-known filenames
    if name == "main.cf":
        return "postfix"
    if name in {"ssh_config", "sshd_config"}:
        return "ssh"

    head = _read_head(path)

    # Content sniffing
    if _looks_like_systemd(head):
        return "systemd"

    # Ambiguous .conf/.cf defaults to INI-ish if no better match
    if suffix in {".conf", ".cf"}:
        if name == "main.cf":
            return "postfix"
        if _looks_like_ssh_config(head):
            return "ssh"
        return "ini"

    # Fallback: treat as INI-ish
    return "ini"


class ConfigParseError(Exception):
    """Raised when a source config file cannot be parsed as its format.

    Each underlying parser (json, tomllib, PyYAML, defusedxml/ElementTree,
    configparser) raises its own exception type on malformed input. Without a
    single normalised error, a malformed file -- which is entirely expected when
    JinjaTurtle is pointed at harvested, attacker-influenceable config -- would
    escape as an unhandled traceback (e.g. ``xml.etree.ElementTree.ParseError``
    on an XML file whose element name is not well-formed). ``parse_config``
    converts every such failure into this one type so the CLI can fail closed
    with a clean message and a non-zero exit code, and so library callers (such
    as Enroll, which falls back to copying the raw file) have a single, stable
    exception to catch.

    Note: defusedxml's *security* exceptions (``EntitiesForbidden``,
    ``DTDForbidden``, ...) are intentionally NOT folded into this type. They
    signal an attempted XXE/entity-expansion attack rather than a benign
    malformed file, and must propagate unchanged so callers can tell the two
    apart.
    """


def _build_malformed_config_errors() -> tuple[type[BaseException], ...]:
    """Return the concrete "this file is malformed" exception types to catch.

    Deliberately specific. In particular we must avoid catching plain
    ``ValueError``: defusedxml's ``EntitiesForbidden``/``DTDForbidden`` subclass
    ``ValueError``, and those are security signals that must NOT be swallowed.
    """

    import configparser
    import json
    from xml.etree.ElementTree import ParseError as _XMLParseError  # nosec

    import yaml as _yaml

    errs: list[type[BaseException]] = [
        _XMLParseError,
        json.JSONDecodeError,
        configparser.Error,
        _yaml.YAMLError,
        UnicodeDecodeError,
    ]
    try:
        import tomllib

        errs.append(tomllib.TOMLDecodeError)
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
        try:
            import tomli  # type: ignore

            errs.append(tomli.TOMLDecodeError)
        except ModuleNotFoundError:
            pass
    return tuple(errs)


_MALFORMED_CONFIG_ERRORS = _build_malformed_config_errors()


def parse_config(path: Path, fmt: str | None = None) -> tuple[str, Any]:
    """
    Parse config file into a Python object.
    """
    fmt = detect_format(path, fmt)
    handler = _HANDLERS.get(fmt)
    if handler is None:
        raise ValueError(f"Unsupported config format: {fmt}")
    try:
        parsed = handler.parse(path)
        # Make sure datetime objects are treated as strings (TOML, YAML). This
        # walks the parsed object recursively, so keep it inside the try where a
        # RecursionError from a pathological structure is normalised below.
        parsed = _stringify_timestamps(parsed)
    except ConfigParseError:
        raise
    except _MALFORMED_CONFIG_ERRORS as exc:
        # Normalise the per-parser "this file is malformed" errors into one
        # json.JSONDecodeError / tomllib.TOMLDecodeError (ValueError
        # subclasses), PyYAML's YAMLError, configparser.Error, and
        # xml.etree.ElementTree.ParseError (raised by defusedxml on XML whose
        # structure/element name is not well-formed). A bad input file is
        # expected when parsing harvested config, so fail closed with a clean
        # error instead of an unhandled traceback.
        #
        # IMPORTANT: this deliberately does NOT catch defusedxml's security
        # exceptions (EntitiesForbidden, DTDForbidden, ...). Those signal an
        # attempted XXE/entity-expansion attack and must propagate unchanged so
        # callers (and tests) can distinguish "malformed" from "malicious".
        raise ConfigParseError(f"could not parse {path} as {fmt}: {exc}") from exc
    except RecursionError as exc:
        # A deeply-nested or self-referential structure (e.g. a recursive YAML
        # anchor) can exhaust the Python stack while walking the parsed object.
        # The YAML handler already rejects reference cycles up front; this is a
        # format-agnostic backstop so any such input fails closed with a clean
        # message instead of a stack-overflow traceback.
        raise ConfigParseError(
            f"could not parse {path} as {fmt}: input is too deeply nested "
            "or self-referential"
        ) from exc

    return fmt, parsed


def analyze_loops(fmt: str, parsed: Any) -> list[LoopCandidate]:
    """
    Analyze parsed config to find loop opportunities.

    JSON files are intentionally kept scalar/index-based instead of being
    collapsed into generated loops. JSON is commonly checked byte-for-byte by
    configuration management tools, and preserving inline arrays/objects is
    more valuable than reducing variable count.
    """
    if fmt == "json":
        return []

    analyzer = LoopAnalyzer()
    candidates = analyzer.analyze(parsed, fmt)

    # Filter by confidence threshold
    return [c for c in candidates if c.confidence >= LoopAnalyzer.MIN_CONFIDENCE]


def flatten_config(
    fmt: str, parsed: Any, loop_candidates: list[LoopCandidate] | None = None
) -> list[tuple[tuple[str, ...], Any]]:
    """
    Flatten parsed config into (path, value) pairs.

    If loop_candidates is provided, paths within those loops are excluded
    from flattening (they'll be handled via loops in the template).
    """
    handler = _HANDLERS.get(fmt)
    if handler is None:
        raise ValueError(f"Unsupported format: {fmt}")

    all_items = handler.flatten(parsed)

    if not loop_candidates:
        return all_items

    # Build set of paths to exclude (anything under a loop path)
    excluded_prefixes = {candidate.path for candidate in loop_candidates}

    # Filter out items that fall under loop paths
    filtered_items = []
    for item_path, value in all_items:
        # Check if this path starts with any loop path
        is_excluded = False
        for loop_path in excluded_prefixes:
            if _path_starts_with(item_path, loop_path):
                is_excluded = True
                break

        if not is_excluded:
            filtered_items.append((item_path, value))

    return filtered_items


def _path_starts_with(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    """Check if path starts with prefix."""
    if len(path) < len(prefix):
        return False
    return path[: len(prefix)] == prefix


def generate_ansible_yaml(
    role_prefix: str,
    flat_items: list[tuple[tuple[str, ...], Any]],
    loop_candidates: list[LoopCandidate] | None = None,
) -> str:
    """
    Create Ansible YAML for defaults/main.yml.
    """
    defaults: dict[str, Any] = {}

    # Add scalar variables
    for path, value in flat_items:
        var_name = make_var_name(role_prefix, path)
        defaults[var_name] = value  # No normalization - keep original types

    # Add loop collections
    if loop_candidates:
        for candidate in loop_candidates:
            var_name = make_var_name(role_prefix, candidate.path)
            defaults[var_name] = candidate.items

    return dump_yaml(defaults, sort_keys=True)


def generate_jinja2_template(
    fmt: str,
    parsed: Any,
    role_prefix: str,
    original_text: str | None = None,
    loop_candidates: list[LoopCandidate] | None = None,
) -> str:
    """
    Generate a Jinja2 template for the config.
    """
    handler = _HANDLERS.get(fmt)

    if handler is None:
        raise ValueError(f"Unsupported format: {fmt}")

    # Check if handler supports loop-aware generation
    if hasattr(handler, "generate_jinja2_template_with_loops") and loop_candidates:
        template = handler.generate_jinja2_template_with_loops(
            parsed, role_prefix, original_text, loop_candidates
        )
    else:
        # Fallback to original scalar-only generation
        template = handler.generate_jinja2_template(
            parsed, role_prefix, original_text=original_text
        )

    # Defence in depth: independently verify that the finished template contains
    # only JinjaTurtle-emitted constructs.  If any handler failed to neutralise
    # verbatim source text, the un-escaped payload shows up here as a live tag
    # and generation aborts instead of emitting an injectable template.
    verify_jinja2_template_safe(template)

    # Format-specific backstop: JinjaTurtle never emits Jinja inside a JSON object
    # key, so a live construct in key position means source key text leaked into
    # the template unescaped. This is independent of per-handler escaping.
    if fmt == "json":
        verify_no_live_jinja_in_json_keys(template)

    return template


def _stringify_timestamps(obj: Any) -> Any:
    """
    Recursively walk a parsed config and turn any datetime/date/time objects
    into plain strings in ISO-8601 form.

    This prevents Python datetime objects from leaking into YAML/Jinja, which
    would otherwise reformat the value (e.g. replacing 'T' with a space).

    This commonly occurs otherwise with TOML and YAML files, which sees
    Python automatically convert those sorts of strings into datetime objects.
    """
    if isinstance(obj, dict):
        return {k: _stringify_timestamps(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_timestamps(v) for v in obj]

    # TOML & YAML both use the standard datetime types
    if isinstance(obj, datetime.datetime):
        # Use default ISO-8601: 'YYYY-MM-DDTHH:MM:SS±HH:MM' (with 'T')
        return obj.isoformat()
    if isinstance(obj, (datetime.date, datetime.time)):
        return obj.isoformat()

    return obj
