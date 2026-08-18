# Desktop Bundles and the Managed Runtime

This directory documents the desktop-bundle restack. The restack gives
Hermes one install model, one pin table, and one update path across every
distribution: source checkouts, the desktop app, Docker, and Nix.

The documents here are for developers who change the install, the update,
or the bundling code. Each document covers one layer of the stack.

## Document map

| Document | Covers |
|---|---|
| `runtime-pins.md` | The pin table, the tool store, the provisioner, and the environment assembler. |
| `bundling.md` | The desktop bundle: variants, payload staging, the build script, and CI. |
| `installers.md` | The install scripts for POSIX and Windows, the dev-checkout wrapper, and the uninstaller. |
| `updating.md` | `hermes update`, the post-update phase, boot bootstrap, and the old-updater contract. |
| `versioning-and-channels.md` | SemVer tags, the install stamp, version display, and update channels. |

## The two-axis install model

Every install of Hermes is a point on two axes.

| Axis | Values | Who owns it |
|---|---|---|
| Update mechanism | `self`, `electron-updater`, `external` | The install stamp, read by `installation/tree.py` |
| Runtime source | Managed tools, or system tools | `installation/registry.py` |

**A `self` install** is a git checkout. `hermes update` owns it: the
checkout pulls new code and then syncs its own environment.

**An `electron-updater` install** is a desktop bundle. It replaces its
own artifact, runtime included, from a release feed.

**An `external` install** is a sealed tree whose steward replaces it
wholesale. The stamp names that steward in its `distribution` field:
`docker` or `nix`. A sealed tree cannot provision itself. Its steward
builds the runtime tools into the artifact at build time.

The stamp field is the only answer to "who updates this". An earlier
design also kept a `.hermes-install.json` manifest with its own
`installMode`, and the two records disagreed. The manifest is gone, and
so is eject.

Update channels apply wherever the mechanism is not `external`, and they
are recorded per install rather than per home.

## Glossary

| Term | Meaning |
|---|---|
| Pin table | `installation/runtime-pins.json`. The one table of exact tool versions, URLs, and digests. |
| Tool store | `~/.hermes/tools/`. Machine-wide directory that holds managed tool bytes. |
| Facts | `runtimes.json` in the runtime dir. What one install actually provisioned. |
| Runtime dir | `<install>/.hermes-runtime/`. Install-scoped facts, caches, and packaged tool trees. |
| Sidecar | A managed tool binary (node, uv, git, gh, ripgrep, a browser engine) that ships beside the code instead of relying on a system copy. |
| Install stamp | `install-stamp.json`. Build-time provenance baked into every packaged artifact. |
| Steward | The system that replaces a sealed tree: the desktop app, Docker, or Nix. |
| Channel | Which releases one install tracks: `main`, `stable`, or `nightly`. Recorded per install under `update.installs.<sha16>`. The desktop updater publishes to its own `latest`, `nightly`, `light`, and `light-nightly` feeds. |
| Provisioner | `installation/provisioner.py`. The one engine that downloads, verifies, and publishes managed tools. |
| Payload | The `agent-payload/` directory inside a bundled desktop app. It holds the whole agent runtime. |
