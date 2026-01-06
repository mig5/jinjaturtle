from __future__ import annotations

from pathlib import Path

import jinjaturtle.core as core


def test_postfix_main_cf_parsing_and_template(tmp_path: Path) -> None:
    p = tmp_path / "main.cf"
    p.write_text(
        "# comment\n"
        "myhostname = mail.example.com\n"
        "mynetworks = 127.0.0.0/8\n"
        "    [::1]/128\n",
        encoding="utf-8",
    )

    fmt, parsed = core.parse_config(p)
    assert fmt == "postfix"

    flat = core.flatten_config(fmt, parsed)
    assert (("myhostname",), "mail.example.com") in flat
    assert any(
        path == ("mynetworks",) and value.startswith("127.0.0.0/8")
        for path, value in flat
    )

    template = core.generate_jinja2_template(
        fmt, parsed, role_prefix="role", original_text=p.read_text(encoding="utf-8")
    )
    assert "myhostname = {{ role_myhostname }}" in template
    assert "mynetworks = {{ role_mynetworks }}" in template
    assert "# comment" in template
