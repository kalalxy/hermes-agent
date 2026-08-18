# Installer Scripts

This document explains the installers and the uninstaller. Before this
restack, each installer carried its own copy of the install logic: its
own uv installer and its own dependency tiers.
Now the installers delegate to the shared engine. The engine has four
parts, and every installer runs the same four:

1. Bootstrap the pinned uv from the pin table.
2. Create the venv and install dependencies (`hermes_cli/venv_sync`).
3. Provision the managed runtime tools
   (`python -m installation.provisioner`).
4. Run the post-update steps (`python -m hermes_cli.post_update`).

What remains in each installer is what is unique to that path: OS
detection, package-manager logic, and where to put the `hermes` command.

## install.sh (Linux, macOS)

`scripts/install.sh` is the POSIX installer:

```
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

The script boots uv itself from a generated fragment at the top of the
file. The fragment comes from the same pin table as everything else
(`scripts/gen-bootstrap-pins.py` regenerates it). The first tool the
installer runs is therefore digest-verified against the same authority
as every later tool.

The notable options:

| Option | Meaning |
|---|---|
| `--no-venv` | Install without a venv (pip into the interpreter). |
| `--skip-setup` | Do not run the setup wizard. |
| `--branch <name>` | Clone this branch instead of `main`. |
| `--commit <sha>` | Check out this exact commit. |
| `--tag <tag>` | Check out this exact tag. |
| `--skip-browser` | Do not stage the browser engine. |
| `--skip-computer-use` | Do not install the computer-use driver. |

The script clears an inherited `PYTHONPATH` and `PYTHONHOME` before it
runs. A pre-set `PYTHONPATH` can force pip and entrypoints to import a
different checkout than the one being installed. That makes fresh
installs appear broken or stale. The script also sets `UV_NO_CONFIG=1`,
so uv does not discover a config file from the wrong user's home under
`sudo -u <user>`.

Root installs use an FHS layout: code at `/usr/local/lib/hermes-agent`,
command at `/usr/local/bin/hermes`, data still at `$HERMES_HOME`. The
layout matches Claude Code and Codex CLI and keeps Docker bind-mounted
volumes lean.

The installer also writes the install manifest (`.hermes-install.json`)
and the install stamp, so the new tree can tell its own story about
where it came from.

## install.ps1 (Windows)

`scripts/install.ps1` is the Windows installer:

```
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

Windows gets no system git: the provisioner always stages PortableGit,
and the script's own git operations use it. The default home is
`$env:LOCALAPPDATA\hermes`.

Reproducible installs pass `-Commit`, `-Tag`, or `-Branch`, in that
precedence order. `-ForceCommit` applies a commit even when it rolls an
existing install BACKWARDS. Without it, a stale baked-in commit cannot
downgrade a current checkout.

The script exposes a stage protocol for programmatic drivers. The
desktop GUI's onboarding wizard, CI, and the Tauri bootstrap installer
all drive it through `-Manifest`, `-Stage`, `-ProtocolVersion`,
`-Json`, and `-NonInteractive`. CLI users running the canonical
one-liner never touch these flags.

Two Windows-specific behaviors matter:

- **8.3 path normalization.** Windows generates a short alias for a
  user-profile folder whose name exceeds the 8.3 limit. Explorer shows
  one spelling and the installer receives another. The script
  normalizes both to the long form so path checks agree.
- **`-IncludeDesktop`** builds `apps/desktop` into a launchable
  `Hermes.exe`. Hermes-Setup.exe passes this flag, so a GUI install
  ends up with a launchable desktop binary. The Electron app's own
  bootstrap runner omits it, because it runs install.ps1 from inside an
  already-launched `Hermes.exe`. Building there overwrites the live
  binary on disk and fails.

The script also has `-ShowResolvedPaths` (print the resolved paths as
JSON and exit) and `-Ensure` mode (the `dep_ensure.py` entry point
for lazy dependency checks). The PowerShell scripts are gated on the
real PowerShell parser in tests. A syntax break then fails CI on every
OS.

## setup-hermes.sh (dev checkouts)

`setup-hermes.sh` is for developers who cloned the repo manually. It
used to be a fourth, parallel implementation of "install Hermes". It is
now a wrapper over the same engine: pinned uv from the generated
fragment, `venv_sync`, the provisioner, and `post_update`. The only
logic that lives here is what is unique to a dev checkout: where to
symlink the CLI (`~/.local/bin`).

## The uninstaller

`hermes uninstall` refuses to remove code from a sealed tree. The
steward put the code there, so the steward removes it. The refusal text
is per steward and per OS:

- Desktop app: remove it through the OS app settings, then delete data
  from Settings, About in the app.
- Docker: remove the container and image.
- Nix: remove the package from the flake or profile, then rebuild.

Data removal is a separate, explicit step: `hermes uninstall --data`.

## The Tauri bootstrap installer

`apps/bootstrap-installer` is Hermes-Setup, the signed Tauri
bootstrap installer. It spawns a single window pointed at a React
frontend and drives `install.ps1` with `-IncludeDesktop`.

The binary has two modes, resolved from its arguments:

- Bare launch (double-click, first-run onboarding) runs the install
  flow.
- `--update`, spawned by the desktop app's Update button, runs the
  update flow.

The mode routes the whole frontend, so the same binary serves first-run
install and in-app update handoff.
