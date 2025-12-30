# JinjaTurtle

<div align="center">
  <img src="https://git.mig5.net/mig5/jinjaturtle/raw/branch/main/jinjaturtle.svg" alt="JinjaTurtle logo" width="240" />
</div>

JinjaTurtle is a command-line tool to help you generate Jinja2 templates and
Ansible inventory from a native configuration file (or files) of a piece of
software.

## How it works

 * The config file(s) is/are examined
 * Parameter key names are generated based on the parameter names in the
   config file. In keeping with Ansible best practices, you pass a prefix
   for the key names, which should typically match the name of your Ansible
   role.
 * A Jinja2 file is generated from the file with those parameter key names
   injected as the `{{ variable }}` names.
 * An Ansible inventory YAML file is generated with those key names and the
   *values* taken from the original config file as the default vars.

By default, the Jinja2 template and the Ansible inventory are printed to
stdout. However, it is possible to output the results to new files.

## What sort of config files can it handle?

TOML, YAML, INI, JSON and XML-style config files should be okay. There are always
going to be some edge cases in very complex files that are difficult to work
with, though, so you may still find that you need to tweak the results.

For XML and YAML files, JinjaTurtle will attempt to generate 'for' loops
and lists in the Ansible yaml if the config file looks homogenous enough to
support it. However, if it lacks the confidence in this, it will fall back to
using scalar-style flattened attributes.

You may need or wish to tidy up the config to suit your needs.

The goal here is really to *speed up* converting files into Ansible/Jinja2,
but not necessarily to make it perfect.

## Can I convert multiple files at once?

Certainly! Pass the folder name instead of a specific file name, and JinjaTurtle
will convert any files it understands in that folder, storing all the various
vars in the destination defaults yaml file, and converting each file into a
Jinja2 template per file type.

If all the files had the same 'type', there'll be one Jinja2 template.

You can also pass `--recursive` to recurse into subfolders.

Note: when using 'folder' mode and multiple files of the same type, their vars
will be listed under an 'items' parent key in the yaml, each with an `id` key.
You'll then want to use a `loop` in Ansible later, e.g:

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

### Fedora 42

```bash
sudo rpm --import https://mig5.net/static/mig5.asc

sudo tee /etc/yum.repos.d/mig5.repo > /dev/null << 'EOF'
[mig5]
name=mig5 Repository
baseurl=https://rpm.mig5.net/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://mig5.net/static/mig5.asc
EOF

sudo dnf upgrade --refresh
sudo dnf install jinjaturtle
```

### From PyPi

```
pip install jinjaturtle
```

### From this git repository

Clone the repo and then run inside the clone:

```
poetry install
```

### AppImage

Download the AppImage from the Releases and make it executable, and put it
on your `$PATH`.

## How to run it

Say you have a `php.ini` file and you are in a directory structure like an
Ansible role (with subfolders `defaults` and `templates`):

```shell
jinjaturtle php.ini \
  --role-name php \
  --defaults-output defaults/main.yml \
  --template-output templates/php.ini.j2
```

## Full usage info

```
usage: jinjaturtle [-h] -r ROLE_NAME [-f {json,ini,toml,yaml,xml}] [-d DEFAULTS_OUTPUT] [-t TEMPLATE_OUTPUT] config

Convert a config file into Ansible inventory and a Jinja2 template.

positional arguments:
  config                Path to the source configuration file (TOML or INI-style).

options:
  -h, --help            show this help message and exit
  -r, --role-name ROLE_NAME
                        Ansible role name, used as variable prefix (e.g. cometbft).
  -f, --format {ini,json,toml,xml}
                        Force config format instead of auto-detecting from filename.
  -d, --defaults-output DEFAULTS_OUTPUT
                        Path to write defaults/main.yml. If omitted, default vars are printed to stdout.
  -t, --template-output TEMPLATE_OUTPUT
                        Path to write the Jinja2 config template. If omitted, template is printed to stdout.
```


## Found a bug, have a suggestion?

You can e-mail me (see the pyproject.toml for details) or contact me on the Fediverse:

https://goto.mig5.net/@mig5
