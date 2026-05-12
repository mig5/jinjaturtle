%global upstream_version 0.5.0

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
* Tue May 11 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Support ssh configs
* Tue Jan 06 2026 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Support converting systemd files and postfix main.cf
* Tue Dec 30 2025 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Support converting a directory (optionally recursively) instead of just an individual file.
* Sat Dec 27 2025 Miguel Jacq <mig@mig5.net> - %{version}-%{release}
- Initial RPM packaging for Fedora 42
