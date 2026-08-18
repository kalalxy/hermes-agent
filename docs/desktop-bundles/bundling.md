# Desktop Bundling

This document explains how the desktop installers are built. It covers
the three variants, the payload that ships inside a bundle, the build
script, and the release workflow. For the step-by-step build recipe,
read `apps/desktop/BUILDING.md`.

## The three variants

One build-time selector, `HERMES_DESKTOP_VARIANT`, chooses the artifact
kind. The install stamp records the choice in its `payload` field.

| Variant | Payload | What the app does |
|---|---|---|
| Unset (default) | `bootstrap` | The artifact carries no runtime. First launch bootstraps a local install. |
| `bundled` | `bundled` | The full agent runtime ships inside the artifact resources. The app runs the backend directly from there. |
| `light` | `light` | No runtime at all. Hermes Light is a remote-only client. It connects to remote gateways. |

The `light` variant is a separate app to the OS and to the updater. It
has its own app ID, its own names, and its own update feed. Both
variants can therefore install and update side by side. A Python process
must never read a light stamp: the artifact contains no Python, and the
readers raise on it rather than limp.

Bundled and light artifacts both pin a release tag. electron-updater
keys on that tag, so a tagless artifact of either kind cannot update
itself. The staging script refuses to build one.

## Product identity

`apps/desktop/product-identity.cjs` is the single source for every
name-shaped value a variant owns: display name, app ID, updater channel,
protocol scheme, and Pascal-case names for MSIX and filenames.

The build bakes this object into the main bundle as the
`__HERMES_PRODUCT_IDENTITY__` define, the same mechanism as the install
stamp. The packaged artifact and the runtime code can never disagree
about who they are. `apps/desktop/electron/product-identity.ts` is the
typed runtime accessor.

## The payload

`apps/desktop/scripts/stage-agent-payloads.mjs` assembles the payload
into `apps/desktop/build/agent-payload/`. The payload is a runtime dir:
a self-contained facts-and-bytes directory, exactly what the Python
provisioner produces when `--runtime-dir` names both bases.

A complete payload contains:

| Item | Contents |
|---|---|
| `manifest.json` | Schema version, tag, commit, platform, arch, build time. |
| `runtimes.json` | The facts. One record per staged tool, each with the path to its binary, relative to the payload root. |
| `repo/` | The plain source tree at the release tag, from `git archive`, without `.git`. It carries the prebuilt JS surfaces (ui-tui dist, dashboard `web_dist`) and the build stamp. |
| `python/` | A uv-managed CPython. Its own site-packages carries `hermes-bundle.pth` with RELATIVE paths to `repo/` and `site-packages/`, so the interpreter resolves the runtime wherever the app bundle sits. No venv, no `PYTHONPATH`. |
| `site-packages/` | The full dependency tree from `uv.lock`, installed at build time with `pip install --target` on the payload interpreter. The backend runs directly from here. Nothing materializes at first launch. |
| Store entries | One directory per managed tool, named `<tool>-<version>-<target>`: `uv`, `node`, `gh`, `ripgrep`, and `git` on Windows. The payload is its own tool store, so the names match the ones a source install writes into `~/.hermes/tools/`. |

The staging script does not lay the tool trees out itself. It runs
`python -m installation.provisioner --runtime-dir <payload> --target
<target>`, the same engine a source install runs, and the provisioner
writes both the store entries and `runtimes.json`. One engine, so a
payload cannot be laid out differently from a source install.

`runtimes.json` is the layout authority. Nothing reads a hardcoded path
like `node/node.exe`: a hardcoded map here diverged silently when the
store-entry naming landed, and every bundled build died on it. The arch
audit resolves each binary through its fact, and so does the app.

Git is on Windows only, and the payload follows the pin table rather
than restating the rule. Windows needs the bash that ships inside
PortableGit. On macOS and Linux `installation.git.git_path()` takes the
machine's git when it clears the version floor, so those targets stage
no git and the pin table records the reason.

Payload staging stays dormant unless `HERMES_DESKTOP_VARIANT=bundled`.
Without it, the script writes a stub manifest marked `external: true`,
and the app resolves to an external backend. The desktop app's
`resolvePayload` treats a missing item directory as a damaged artifact,
never as a fallback.

## Architecture checks in staging

A payload is complete or the build fails. There is no per-item skip.

**No cross-platform wheels.** One CI runner per (os, arch) pair
assembles the payloads. electron-builder needs per-OS runners for
signing anyway, so the script fetches wheels natively with
`uvx pip wheel --only-binary=:all:` on the target runner. The user
machine never compiles.

**Two exceptions build from source on the runner.** Some pinned
packages publish no wheel for a target. The `sourceBuild` list names
them per target: `cryptography` on darwin-x64, and `cryptography`,
`httptools`, `ruamel-yaml-clib`, `pywinpty`, and `pyyaml` on
win32-arm64. pip builds the EXACT pinned version from its sdist on the
build runner, which needs MSVC arm64 and Rust there. The user machine
still never compiles.

The arch gate. Every staged binary must prove the target
architecture. `uv --version` and `python -VV` print their build triple.
Node reports `process.arch`. A mismatch fails the build, because a
wrong-arch binary can run on the build host through emulation and ship
broken. After provisioning, `assertPayloadArch` re-checks every required
tool (node, uv, git, gh, ripgrep) by header inspection (PE, Mach-O, or
ELF), because the build host usually cannot execute what it staged. It
resolves each binary through its fact in `runtimes.json`, and it refuses
a fact that records an absolute path: an absolute path is a machine
binary outside the payload, and a sealed artifact must carry its own
tools.

**The staging cache.** The `python/` and `site-packages/` stages
dominate build time. Their content is a pure function of the target,
the Python version, and the requirements text, so the cache key covers
exactly those inputs. A cache hit reuses the trees. Everything
tag-dependent (repo, dist-info, manifest) is staged fresh every run.

## The build script

`scripts/build-bundled-desktop.mjs` drives the whole build. It always
runs every step. A skipped step is a different artifact, and a different
artifact is not a reproduction.

1. Preflight. The host needs `uv`, `git`, `npm`, and `tar`. The host
   node and npm must satisfy the `package.json` engines, and uv must
   print a build triple. The payload embeds these exact host versions,
   so gate equals embed.
2. `npm ci` at the repo root.
3. Build the JS surfaces: ui-tui (with hermes-ink) and the dashboard
   SPA.
4. Download the payload node dist (bundled only).
5. `npm run build` in `apps/desktop` with the variant exported.
6. Run electron-builder.

The script takes `--tag=vX.Y.Z` and `--variant=bundled|light`.
Everything after `--` goes to electron-builder verbatim. CI appends its
signing configuration that way. Local builds are unsigned. Signing is
CI's job.

## electron-builder configuration

`apps/desktop/electron-builder.config.cjs` is the whole configuration,
one file. There is no `build` field in package.json. The file is a
`.cjs` module for two reasons:

- `mac.sign.ignore` must be a function. osx-sign's binary-content probe
  flags plain binary resources (payload GIFs, wheels, zips) as signable.
  Signing those is wrong, and each bogus signing hit Apple's timestamp
  service until it refused. The function scopes signing to real Mach-O
  files by magic-number inspection.
- The variant is decided at require time from
  `HERMES_DESKTOP_VARIANT`. The whole config derives from the one
  `light` flag.

The electron version comes from `package.json` `devDependencies`, not
from a second copy. The publish feed's owner and repo come from
`GITHUB_REPOSITORY`, so forks publish to their own feed automatically.

Windows targets are NSIS and MSIX. NSIS is the one-click per-user
installer with electron-updater. MSIX exists for Store and sideload
installs and for the Windows Copilot hardware key. electron-updater
does not update MSIX installs. The MSIX manifest registers the app as a
Copilot key provider through a `uap3:AppExtension` fragment, and the
Start-tile logos come from `assets/appx/` staged into the build dir.

macOS targets are DMG and zip, signed and notarized when the secrets
are present. Linux is an AppImage, unsigned.

## Signing

**Windows** uses Azure Trusted Signing. The config composes the
`win.sign` block from `AZURE_SIGN_*` environment variables at config
load time. The publisher name contains spaces and commas, which do not
survive the cmd.exe hops between npm and the builder, so the values
travel in the environment, never as `-c` arguments.

CI authenticates as a workload identity. The job mints its GitHub OIDC
token into a file, reminted every 4 minutes because signing runs for
the better part of an hour, and points `AZURE_FEDERATED_TOKEN_FILE` at
it. `AZURE_TOKEN_CREDENTIALS=prod` restricts the Azure credential chain
to Environment, WorkloadIdentity, and ManagedIdentity. The first fails
instantly, the second redeems the token file, and the third is never
reached. The dev-tool credentials all spawn subprocesses, which wedged
the x64-emulated signtool on the arm64 runner for 35+ minutes.

**macOS** uses electron-builder's builtin notarization with the
`APPLE_API_KEY` / `APPLE_API_KEY_ID` / `APPLE_API_ISSUER` variables.
`APPLE_API_KEY` must be a PATH to the `.p8` key. The value travels
verbatim into `notarytool --key`, which takes a file path. The release
workflow keeps the raw PEM in a secret and writes it to a runner-temp
file. Without the variables, the build skips notarization. Forks and
local builds work unsigned.

## The release workflow

`.github/workflows/desktop-bundled-release.yml` builds all targets on a
per-OS runner matrix. Two triggers exist:

- A push of a final release tag (`vX.Y.Z`). This is the normal release
  path.
- `workflow_dispatch` with an explicit tag, for re-runs and dry runs.
  The release upload is opt-in there.

The matrix is variant times target: every target builds both the
bundled installer and the light client. The signing secrets live in the
`release-signing` environment, whose deployment policy admits only
`main` and `v*` tags. A dispatch against a work branch cannot reach the
secrets at all. On forks, GitHub treats the environment as empty, and
the build stays unsigned.

Two runner notes: the mac lanes kill Spotlight and XProtect first
(XProtect is documented as a cause of `hdiutil: couldn't eject` on DMG
detach), and the win32-arm64 runner carries MSVC arm64 and Rust for the
`sourceBuild` packages.
