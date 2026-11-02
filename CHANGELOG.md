# Changelog

## 3.1.0

- Packaged as `appman-cli` with the `appman` console script.
- XDG paths for installed apps, registry, plugins, deb builds, and cache.
- Legacy `~/local` detection plus `appman migrate --dry-run/--apply`.
- Built-in plugins with user overrides from the XDG plugin directory.
- `install.sh` standalone zipapp installer with SHA256 verification.
- `doctor`, JSON output, and `check --exit-code` retained from 3.0.

## 3.0.0

- Subcommand CLI: install, scan, list, check, upgrade, uninstall, rollback,
  history, repair, set-source, adopt, plugins, doctor, version, help.
- Removed ambiguous `-u`/`-U` action flags.
