from __future__ import annotations

from pathlib import Path

import jinjaturtle.core as core


def test_systemd_unit_repeated_keys(tmp_path: Path) -> None:
    p = tmp_path / "demo.service"
    p.write_text(
        "[Service]\n" "ExecStart=/bin/echo one\n" "ExecStart=/bin/echo two\n",
        encoding="utf-8",
    )

    fmt, parsed = core.parse_config(p)
    assert fmt == "systemd"

    flat = core.flatten_config(fmt, parsed)
    assert (("Service", "ExecStart", "0"), "/bin/echo one") in flat
    assert (("Service", "ExecStart", "1"), "/bin/echo two") in flat

    template = core.generate_jinja2_template(
        fmt, parsed, role_prefix="role", original_text=p.read_text(encoding="utf-8")
    )
    assert "ExecStart={{ role_service_execstart_0 }}" in template
    assert "ExecStart={{ role_service_execstart_1 }}" in template
