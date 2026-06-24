%global upstream_version 0.5.7

Name:           jinjaturtle
Version:        %{upstream_version}
Release:        1%{?dist}.jinjaturtle1
Summary:        Convert config files into Ansible vars and Jinja2 templates.

License:        GPL-3.0-or-later
URL:            https://git.mig5.net/mig5/jinjaturtle
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-devel
BuildRequires:  python3-poetry-core

Requires: python3-yaml
Requires: python3-tomli
Requires: python3-defusedxml
Requires: python3-jinja2

%description
Convert config files into Ansible defaults and Jinja2 templates.

%prep
%autosetup -n jinjaturtle

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files jinjaturtle

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/jinjaturtle

%changelog
* Wed Jun 24 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- More hardening
* Tue Jun 23 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Try to prevent what could lead to execution of embedded jinja in original files when converting
* Sat Jun 20 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- erb support
* Sat Jun 20 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Make templates more faithful to the original file in terms of indentation, newlines, no deserialisation of things like < or >.
- More test coverage
* Fri Jun 19 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Fix loss of comments and True/False to true/false
* Fri Jun 19 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Fix indentation problems with nested dicts
* Fri Jun 19 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Empty dicts and lists are now emitted as leaf defaults.
* Mon May 11 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Support ssh configs
* Tue Jan 06 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Support converting systemd files and postfix main.cf
* Tue Dec 30 2025 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Support converting a directory (optionally recursively) instead of just an individual file.
* Sat Dec 27 2025 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Initial RPM packaging for Fedora 42
