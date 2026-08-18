# Sidecars

A sidecar is a program Hermes runs but does not compile: a browser
engine, a Node service, a CLI. Hermes ships two kinds, and they are
pinned by two different mechanisms. The kind decides where the version
lives, what verifies it, and what happens when the network is hostile.

This document explains both mechanisms, the one place they meet (the
Camoufox handshake), and how to add a sidecar.

## The two kinds

| | Binary sidecar | npm sidecar |
|---|---|---|
| Examples | node, uv, git, gh, ripgrep, camoufox, chromium | the Camoufox driver, the WhatsApp bridge, the Photon sidecar |
| Pinned in | `installation/runtime-pins.json` | a `package-lock.json` beside the sidecar |
| Verified by | A sha256 in the pin table, checked after download | The integrity hash npm records for every dependency |
| Installed by | `installation/provisioner.py` | `npm ci` with the managed npm |
| Lands in | The tool store, `~/.hermes/tools/` | `node_modules/` beside that sidecar |

Both are exact. Neither resolves a version at install time. The
difference is who owns the format: the pin table is ours, and a
lockfile is npm's.

Use a binary pin when the artifact is a release download. Use an npm
sidecar when the thing is a Node package with a dependency tree, because
reimplementing npm's resolver in the pin table would be the wrong trade.

## Binary sidecars

`docs/desktop-bundles/runtime-pins.md` covers the pin table, the store,
and the provisioner in full. The short version:

- One entry per tool, with an exact version, and per target a URL and a
  sha256, or a reason no artifact exists.
- `optional: true` marks a capability nobody downloads until something
  asks. A browser engine is ~650MB, so an install that never browses
  never pays for it.
- The provisioner downloads, verifies the digest, unpacks into a store
  entry named `<tool>-<version>-<target>`, and records a fact.

## npm sidecars

An npm sidecar is a directory with its own `package.json` and
`package-lock.json`, installed with `npm ci`:

| Sidecar | Directory | Drives |
|---|---|---|
| Camoufox driver | `scripts/camofox-browser/` | The pinned Camoufox browser |
| WhatsApp bridge | `scripts/whatsapp-bridge/` | The WhatsApp platform adapter |
| Photon sidecar | `plugins/platforms/photon/sidecar/` | The Photon platform adapter |

`npm ci` is the whole discipline. It installs exactly the lockfile, and
every dependency in that lockfile carries an integrity hash. It fails
rather than resolving anything itself, so a sidecar install cannot pick
up a version the lockfile does not name.

The npm that runs is the managed one, resolved through
`installation.registry.tool_path("npm")`. It is not on the ambient PATH,
so a bare `npm` here finds a system copy or nothing.

## The Camoufox handshake

Camoufox is both kinds at once: a pinned browser binary plus an npm
driver that expects to have downloaded that binary itself. Getting the
two to agree takes two deliberate steps, and both exist because the
library's default behavior is an unpinned download.

**The order.** `_install_browser` in `hermes_cli/dep_ensure.py`
provisions the pinned browser FIRST, then runs `npm ci` for the driver.
Reversed, the driver's postinstall runs `npx camoufox-js fetch`, which
picks a browser from the GitHub releases API by matching a regex against
whatever is newest. That is a ~650MB download plus a 66MB GeoIP
database, chosen at install time, different on two machines that install
on different days.

**The variable.** The install exports `CAMOUFOX_EXECUTABLE` at the path
of the staged browser. One variable does two jobs: the postinstall skips
its fetch (`externalExecutableFromEnv`), and the server launches that
same binary at runtime (`lib/config.js`).

CAUTION: `CAMOUFOX_INSTALL_DIR` does not do this job. The postinstall
looks for a `version.json` in its own `camoufoxCacheDir()` rather than
at that variable, and the variable is not in `FETCH_CHILD_ENV_VARS`, so
the fetch child never receives it. Measured: with `INSTALL_DIR` pointed
at an already-staged copy, the fetch downloaded the full 663MB browser
and the 66MB GeoIP database anyway.

**The version file.** The Camoufox zip unpacks flat and does not contain
`version.json`. camoufox-js writes that file itself after its own
download, and `Version.fromPath()` raises `FileNotFoundError: Version
information not found` without it, which its postinstall reports as a
broken install. So `_stage_camoufox` writes the file after unpacking.
That write is what makes a provisioned browser look installed to the
library.

The split has to match the library's own parse. Its fetcher matches
`camoufox-(.+)-(.+)-<os>.<arch>.zip` greedily and builds
`Version(match[2], match[1])`, release second and version first, so the
pin `152.0.4-beta.28` is version `152.0.4` and release `beta.28`. A
wrong split shows up as a browser that reports the wrong version rather
than as a crash, which is why `_camoufox_version_json` refuses a pin
without both halves instead of guessing.

## Add a binary sidecar

1. Add the entry to `installation/runtime-pins.json`. Give it an exact
   version, and give every target either a `url` plus `sha256` or a
   `{"missing": reason}`. Set `optional: true` when nobody needs it
   until they ask for it. Set `onPath: false` when the artifact is not a
   CLI surface.
2. Make sure the entry validates. `scripts/run_tests.sh
   tests/installation/test_runtime_pins_schema.py` runs the table
   through both validators.
3. Teach `installation/provisioner.py` the binary's path inside its
   archive, in the `_binary_rel` map. A tool whose archive needs more
   than unpacking gets a staging routine, as Camoufox does.
4. When the tool must be present for the desktop bundle, add it to the
   `required` list in `assertPayloadArch`.
5. Run `python -m installation.provisioner --json` on this host and
   confirm the fact it records.

## Add an npm sidecar

1. Create the directory with a `package.json` and a committed
   `package-lock.json`. Commit the lockfile: it is the pin.
2. Install it with `npm ci` through the managed npm, never a bare `npm`
   and never `npm install`. `npm install` resolves versions and
   rewrites the lockfile, which defeats the pin.
3. Give the install a real trigger. A sidecar that installs at import
   time makes every user pay for a capability most of them do not use.
   `hermes_cli/dep_ensure.py` holds the on-demand triggers.
4. When the sidecar drives a pinned binary, provision the binary first
   and point the sidecar at it through the environment, as the Camoufox
   handshake does.

## Verification

```bash
scripts/run_tests.sh tests/installation tests/hermes_cli/test_runtime_registry.py
python -m installation.provisioner --json
```

The first proves the table and the loader agree. The second provisions
this host and prints the facts, which is the only proof that a new pin
downloads, verifies, and unpacks to the path the code expects.
