# JinjaTurtle

<div align="center">
  <img src="https://git.mig5.net/mig5/jinjaturtle/raw/branch/main/jinjaturtle.svg" alt="JinjaTurtle logo" width="240" />
</div>

JinjaTurtle is a command-line tool that helps turn existing native
configuration files into reusable configuration-management templates.

By default it generates:

- a **Jinja2** template; and
- an **Ansible defaults YAML** file containing the variables used by that
  template.

JinjaTurtle does not try to replace configuration-management tools. Its job is
to speed up the boring first pass: take a real config file, discover the values
inside it, replace those values with variables, and write the corresponding
variable data beside the template.

## How it works

JinjaTurtle examines a source config file and keeps the original structure as
much as possible.

For the default Jinja2/Ansible mode:

1. The config file is parsed.
2. Variable names are generated from the config keys and paths.
3. Those variable names are prefixed with `--role-name`, which should usually
   match your Ansible role name.
4. A Jinja2 template is generated with values replaced by `{{ variable }}`
   expressions.
5. An Ansible defaults YAML file is generated with those variables and the
   original values.

By default, the generated variable data and template are printed to stdout. Use
`--defaults-output` and `--template-output` to write them to files.

## Jinja2 / Ansible example

Say you have a `php.ini` file and you are inside an Ansible role with
`defaults/` and `templates/` directories:

```shell
jinjaturtle php.ini \
  --role-name php \
  --defaults-output defaults/main.yml \
  --template-output templates/php.ini.j2
```

Given a source value such as:

```ini
memory_limit = 256M
```

JinjaTurtle will produce a template value like:

```jinja2
memory_limit = {{ php_memory_limit }}
```

and defaults data like:

```yaml
php_memory_limit: 256M
```

## What sort of config files can it handle?

JinjaTurtle supports common structured and semi-structured config formats:

- TOML
- YAML
- INI-style files
- JSON
- XML
- Postfix `main.cf`
- systemd unit files, such as `*.service`, `*.socket`, `*.timer`, and related
  unit types
- OpenSSH-style config files, including `ssh_config`, `sshd_config`, and common
  `*.conf` snippets detected as SSH config

For ambiguous extensions such as `*.conf`, JinjaTurtle uses lightweight content
sniffing. You can always force a handler with `--format`.

For YAML, XML, TOML, INI-style, and other supported structured files,
JinjaTurtle will attempt to generate loops when a repeated structure looks
homogeneous enough. If it is not confident, it falls back to flattened scalar
variables.

Some very complex files will still need manual cleanup. The goal is to speed up
conversion into Jinja2 templates, not to guarantee a perfect final module without
review.

## JSON, quoting, and type preservation

JinjaTurtle tries to preserve rendered config types.

For JSON, it uses JSON-aware expressions rather than plain string substitution.
This avoids generating invalid JSON such as:

```json
{"enabled": True}
```

when the correct rendered JSON should be:

```json
{"enabled": true}
```

This uses Ansible-style JSON filters.

## Can I convert multiple files at once?

Yes. Pass a directory instead of a single file and JinjaTurtle will convert the
files it understands in that directory.

```shell
jinjaturtle ./config-dir \
  --role-name myrole \
  --defaults-output defaults/main.yml \
  --template-output templates/
```

Use `--recursive` to recurse into subdirectories.

In folder mode, variables for multiple files of the same type are grouped under
an `items`-style structure in the generated YAML so that the resulting templates
can be used with loops in Ansible.

For example:

```yaml
- name: Render configs
  template:
    src: config.j2
    dest: "/somewhere/{{ item.id }}"
  loop: "{{ myrole_items }}"
```

## How to install it

### Ubuntu/Debian apt repository

```bash
sudo mkdir -p /usr/share/keyrings
curl -fsSL https://mig5.net/static/mig5.asc | sudo gpg --dearmor -o /usr/share/keyrings/mig5.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/mig5.gpg] https://apt.mig5.net $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/mig5.list
sudo apt update
sudo apt install jinjaturtle
```

### Fedora

```bash
sudo rpm --import https://mig5.net/static/mig5.asc

sudo tee /etc/yum.repos.d/mig5.repo > /dev/null << 'EOF'
[mig5]
name=mig5 Repository
baseurl=https://rpm.mig5.net/$releasever/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://mig5.net/static/mig5.asc
EOF

sudo dnf upgrade --refresh
sudo dnf install jinjaturtle
```

### From PyPI

```bash
pip install jinjaturtle
```

### From this git repository

Clone the repo and then run inside the clone:

```bash
poetry install
```

## Full usage info

```text
usage: jinjaturtle [-h] [-r ROLE_NAME] [--recursive]
                   [-f {ini,json,toml,yaml,xml,postfix,systemd,ssh}]
                   [-d DEFAULTS_OUTPUT] [-t TEMPLATE_OUTPUT]
                   config

Convert a config file into an Ansible defaults file and Jinja2 template.

positional arguments:
  config                Path to a config file OR a folder containing supported
                        config files. Supported: .toml, .yaml/.yml, .json,
                        .ini/.cfg/.conf, .xml, ssh_config/sshd_config

options:
  -h, --help            show this help message and exit
  -r, --role-name ROLE_NAME
                        Role name / variable prefix. In Jinja2 mode this is
                        usually the Ansible role name. Defaults to jinjaturtle.
  --recursive           When CONFIG is a folder, recurse into subfolders.
  -f, --format {ini,json,toml,yaml,xml,postfix,systemd,ssh}
                        Force config format instead of auto-detecting from
                        filename.
  -d, --defaults-output DEFAULTS_OUTPUT
                        Path to write the generated variable YAML. If omitted,
                        it is printed to stdout.
  -t, --template-output TEMPLATE_OUTPUT
                        Path to write the generated config template. If omitted,
                        it is printed to stdout.
```

## Additional supported formats

JinjaTurtle also templates some common bespoke config formats:

- **Postfix main.cf** (`main.cf`) → `--format postfix`
- **systemd unit files** (`*.service`, `*.socket`, etc.) → `--format systemd`
- **OpenSSH config** (`ssh_config`, `sshd_config`, and detected snippets) →
  `--format ssh`

For ambiguous extensions like `*.conf`, JinjaTurtle uses lightweight content
sniffing. You can always force a specific handler with `--format`.

## Security model

JinjaTurtle is frequently pointed at config files that were *harvested* from
real systems, where some content may be influenced by an untrusted party (a
hostname, a login banner, a `GECOS` comment, a "Managed by ..." note). It is
therefore designed so that source content cannot turn into executable template
code.

Two guarantees matter:

1. **Values are data, never code.** Every config *value* is replaced with a
   `{{ variable }}` placeholder in the template, and the original value is stored
   separately in the defaults data. When the template is later rendered,
   the placeholder prints the value as a literal string; Jinja2 does not
   recursively render the *contents* of a variable, so a payload sitting inside
   a value is inert.

2. **Verbatim text is neutralised.** To preserve formatting, JinjaTurtle copies
   comments, blank lines, headers and any unrecognised lines from the source
   into the template. Any template metacharacters in that copied text
   (`{{ }}`, `{% %}`, `{# #}` ) are escaped so they render as the literal
   characters the author wrote, rather than executing.

### Consumer responsibilities

The value guarantee above relies on the downstream renderer being single-pass,
which is the normal case:

- **Ansible**: rendering a template with `template:`/`ansible.builtin.template`
  is single-pass. For defence in depth, treat the generated defaults as
  untrusted input — Ansible already does not re-template variable *contents* by
  default. If you build your own var structures from this data and pass them
  through additional templating, mark untrusted values with the `!unsafe` tag so
  they are never re-evaluated.

In short: render JinjaTurtle output exactly once. Do not feed it back through
another templating pass.

**IMPORTANT**: Always review both the original config files, then the resulting
templates generated by JinjaTurtle, before integrating them into your config
management system!


## Found a bug, have a suggestion?

You can e-mail me; see `pyproject.toml` for details. You can also contact me on
the Fediverse:

https://goto.mig5.net/@mig5
