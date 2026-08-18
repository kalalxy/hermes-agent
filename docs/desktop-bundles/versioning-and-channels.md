# Versioning and Update Channels

This document explains how Hermes versions itself and how a user
chooses which updates to take. Two channel systems exist. The
`main`/`stable` channels serve Git installs. The updater feeds serve
the desktop app.

## SemVer releases

Releases are now SemVer tags: `vX.Y.Z`. The historical CalVer tags
(`v2026.7.20`) still exist in history, but new releases never create
them.

`scripts/release.py` owns the release:

```
python scripts/release.py --bump minor --publish
```

The script does the following:

1. Reads the current version from `hermes_cli/__init__.py`.
2. Computes the next SemVer version.
3. Updates every version file: `hermes_cli/__init__.py`,
   `pyproject.toml`, `uv.lock`, `apps/desktop/package.json`, and
   `package-lock.json`. The version in the desktop manifest stays
   aligned with the root Python package, because the Python package
   owns the canonical value.
4. Generates a changelog from the commits since the last tag.
5. Creates the tag and drafts a GitHub release.

The draft release is the handoff point. The desktop-bundled release
workflow watches for `vX.Y.Z` tag pushes, builds the installers, and
attaches them to the draft. Publishing is a separate,
deliberate step: `gh release edit vX.Y.Z --draft=false`.

The SemVer matcher caps the major at three digits. That is what keeps
a four-digit-year CalVer tag from sorting above every SemVer release
in a numeric comparison.

`--first-release` covers the first release with no previous tag.
`--date` overrides the release-date metadata for a belated release.

## The install stamp

`scripts/write_install_stamp.py` writes `install-stamp.json`. Every
packager calls it: Docker, Nix, and the desktop app. The stamp is the
single provenance record:

| Field | Meaning |
|---|---|
| `commit` | The exact commit, or a zero fallback. |
| `commitDate` | Commit timestamp. |
| `branch` | Branch name, when known. |
| `dirty` | Whether the tree had uncommitted changes. |
| `source` | Where the facts came from: `ci`, `local`, `docker`, `nix`, `fallback`. |
| `distribution` | The steward: `docker`, `nix`, `desktop-app`. |
| `baseVersion` | The package version. |
| `displayVersion` | `baseVersion` plus `+N` distance, or `+?` when dirty. |
| `distance` | Commits since the release tag. |
| `payload` | `bootstrap`, `bundled`, or `light`. |
| `tag` | The release tag, always set for bundled and light. |

The stamp is a constant of the artifact. The desktop build bakes it
into the main bundle as the `__HERMES_INSTALL_STAMP__` define. It
cannot be missing, stale, or edited after signing.

## Version display

`hermes_cli/version_info.py` answers "what version is this?" The
resolution order is fixed:

1. The install stamp, for packaged builds. Authoritative.
2. Live git, for source and dev installs with a `.git` directory.
3. Unknown. No stamp and no git, so the provenance is unknown.

`__version__` remains the package and API version. The display adds a
suffix only when it can prove the distance: `v0.27.0+12` means twelve
commits past the `v0.27.0` tag. A dirty tree with no resolvable
distance shows `+?`.

The distance probe tries the SemVer tag first, then the legacy CalVer
tag. That keeps existing CalVer-tagged releases displaying a correct
distance during the transition.

A light stamp is a hard error for a Python process. The artifact
contains no Python. A Python process reading its own stamp as light
means the artifact was mispackaged. The readers raise rather than
misclassify the tree.

## The desktop updater feeds

The desktop app uses electron-updater, which reads feed files from a
GitHub release. Four feeds exist, one per variant and channel pair:

| Variant | Channel | Feed file |
|---|---|---|
| Hermes (bundled) | `latest` | `latest*.yml` |
| Hermes (bundled), nightly | `nightly` | `nightly*.yml` |
| Hermes Light | `light` | `light*.yml` |
| Hermes Light, nightly | `light-nightly` | `light-nightly*.yml` |

The channel is part of the product identity
(`apps/desktop/product-identity.cjs`), which derives it from the build
tag: a `vX.Y.0-nightly.<timestamp>` tag publishes to the nightly feeds.
One release workflow serves both channels. The feed's owner and repo
come from `GITHUB_REPOSITORY`, so a fork's builds publish to and update
from the fork's own releases. That is the fork-updater-channel
behavior. A fork must not point users at the upstream feed.

## The channel settings, one table

| Setting | Where | Applies to | Values |
|---|---|---|---|
| `update.installs.<sha16>.channel` | `config.yaml`, per install | Any install whose mechanism is not `external` | `main`, `stable`, `nightly` |
| Updater channel | Product identity, build time | Which feed a desktop build PUBLISHES to | `latest`, `nightly`, `light`, `light-nightly` |
| Steward versioning | Install stamp | `external` installs | None. The steward owns it |

The effective channel resolves in two steps:

1. This install's own record, `update.installs.<sha16>.channel`, when it
   names a valid channel.
2. Otherwise the mechanism default: `main` for a `self` source install,
   `stable` for an `electron-updater` bundle.

A source install that asks for `nightly` tracks `main`, and the caller
prints a note. Nightly builds are desktop release artifacts, and a git
checkout tracks branches. The record is per install and never per home,
because one `config.yaml` can serve a checkout, a Docker gateway, and
the desktop app at once.

An `external` install never asks. Its steward owns versioning,
`--set-channel` refuses with the steward's name, and `hermes update`
says so in plain words with the correct steward command.
