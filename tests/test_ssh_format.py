from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined
import yaml

import jinjaturtle.core as core


def _render(template: str, variables: dict) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    return env.from_string(template).render(**variables)


def test_sshd_config_parsing_match_sections_repeated_keys_and_roundtrip(
    tmp_path: Path,
) -> None:
    p = tmp_path / "sshd_config"
    original = (
        "# global sshd settings\n"
        "Port 22\n"
        "ListenAddress 0.0.0.0\n"
        "ListenAddress ::\n"
        "AuthenticationMethods publickey,password publickey,keyboard-interactive\n"
        "Subsystem sftp /usr/lib/openssh/sftp-server # keep sftp\n"
        "Match User deploy Address 10.0.0.0/8\n"
        "    X11Forwarding no\n"
        '    ForceCommand "internal-sftp -u 0002"\n'
    )
    p.write_text(original, encoding="utf-8")

    fmt, parsed = core.parse_config(p)
    assert fmt == "ssh"

    flat = core.flatten_config(fmt, parsed)
    assert (("Port",), "22") in flat
    assert (("ListenAddress", "0"), "0.0.0.0") in flat
    assert (("ListenAddress", "1"), "::") in flat
    assert (
        ("AuthenticationMethods",),
        "publickey,password publickey,keyboard-interactive",
    ) in flat
    assert (
        ("Match", "user_deploy_address_10_0_0_0_8"),
        "User deploy Address 10.0.0.0/8",
    ) in flat
    assert (
        ("Match", "user_deploy_address_10_0_0_0_8", "ForceCommand"),
        "internal-sftp -u 0002",
    ) in flat

    ansible_yaml = core.generate_ansible_yaml("role", flat)
    variables = yaml.safe_load(ansible_yaml)
    template = core.generate_jinja2_template(
        fmt, parsed, role_prefix="role", original_text=original
    )

    assert "ListenAddress {{ role_listenaddress_0 }}" in template
    assert "ListenAddress {{ role_listenaddress_1 }}" in template
    assert "Match {{ role_match_user_deploy_address_10_0_0_0_8 }}" in template
    assert (
        'ForceCommand "{{ role_match_user_deploy_address_10_0_0_0_8_forcecommand }}"'
        in template
    )
    assert _render(template, variables) == original


def test_ssh_config_host_and_match_sections_equal_separator(tmp_path: Path) -> None:
    p = tmp_path / "ssh_config"
    original = (
        "Include /etc/ssh/ssh_config.d/*.conf\n"
        "Host bastion *.corp.example\n"
        "    User ops\n"
        "    ProxyCommand=ssh -W %h:%p jump.example\n"
        'Match exec "test %p = 22"\n'
        "    ServerAliveInterval 30\n"
    )
    p.write_text(original, encoding="utf-8")

    fmt, parsed = core.parse_config(p)
    assert fmt == "ssh"

    flat = core.flatten_config(fmt, parsed)
    assert (("Host", "bastion_corp_example"), "bastion *.corp.example") in flat
    assert (
        ("Host", "bastion_corp_example", "ProxyCommand"),
        "ssh -W %h:%p jump.example",
    ) in flat
    assert (("Match", "exec_test_p_22"), 'exec "test %p = 22"') in flat

    ansible_yaml = core.generate_ansible_yaml("ssh", flat)
    variables = yaml.safe_load(ansible_yaml)
    template = core.generate_jinja2_template(fmt, parsed, role_prefix="ssh")

    assert "ProxyCommand={{ ssh_host_bastion_corp_example_proxycommand }}" in template
    assert _render(template, variables) == original


def test_ssh_config_snippet_conf_detected_by_content(tmp_path: Path) -> None:
    p = tmp_path / "50-hardening.conf"
    p.write_text("PasswordAuthentication no\nPermitRootLogin no\n", encoding="utf-8")

    fmt, parsed = core.parse_config(p)
    assert fmt == "ssh"
    assert (("PasswordAuthentication",), "no") in core.flatten_config(fmt, parsed)
