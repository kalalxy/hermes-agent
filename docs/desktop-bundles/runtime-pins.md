# Runtime Pins, the Tool Store, and the Provisioner

This document explains how Hermes manages its native runtime tools. These
tools are the sidecars: node, npm, uv, git, gh, ripgrep, and the browser
engines. Every install kind uses the same machinery.

The design goal is one authority. Before this restack, each install path
resolved its own node, its own uv, and its own git. The versions
drifted, and a build shipped a tool that the code did not expect. The
pin table ends that: one file names every version, and every consumer
reads it.

## The three stores

The system keeps three kinds of data apart. Each has one owner.

| Store | Location | Owner |
|---|---|---|
| Pins | `installation/runtime-pins.json`, in the repo | Code review |
| Facts | `<install>/.hermes-runtime/runtimes.json` | The provisioner |
| Bytes | `~/.hermes/tools/<tool>-<version>-<target>/` | The provisioner |

**Pins** declare what a tool must be. A pin is an exact version, plus one
download URL and one sha256 per target. There is no range grammar and no
"resolve latest" step. A pin bump is a deliberate edit with new URLs and
new digests. This policy exists for three reasons:

- A "resolve latest" call needs the GitHub API. Unauthenticated callers
  get 60 requests per hour.
- Two builds of the same commit must agree. "Latest" can change between
  them.
- A tool must not change under users without a code review.

**Facts** record what one install actually provisioned: version, path
relative to the tool store, install time, and the derived PATH order.
Only the provisioner writes facts. Everything else reads them.

**Bytes** are the extracted tool trees. The store is machine-wide, so
several installs share one copy of node. A checkout-nested copy cost
about 495 MB per worktree, and worktrees are the normal unit of work
here.

Facts and bytes live apart on purpose. N installs cost N small JSON
files and one copy of node. Nothing links them on disk: a fact names a
store-relative path, so the facts file IS the indirection layer.

The pin table ships as package data. It is not a repo-root file that the
module reaches up for. `HERMES_RUNTIME_PINS` can point packagers at a
copy in their own store path.

## What the table pins

As of this restack, the table pins these tools:

| Tool | Version | Notes |
|---|---|---|
| node | 26.7.0 | Official dist tarballs per target. |
| npm | 12.0.2 | Extends node. Newer than the npm node bundles. |
| uv | 0.12.3 | Carries the Python pin: 3.11.15. |
| git | 2.53.0 | dugite-native on POSIX, PortableGit on Windows. |
| gh | 2.97.0 | GitHub CLI. |
| ripgrep | 15.2.0 | |
| camoufox | 152.0.4-beta.28 | Optional. Browser engine for the Camoufox tool. |
| chromium | 1208 | Optional. Off PATH. No win32-arm64 build. |
| chromium-headless-shell | 1208 | Optional. Off PATH. No win32-arm64 build. |

Three table features shape the rest of the design:

**`extends`** says that one tool plugs into another. npm is unpacked by
running the node it extends. The table derives two orders from these
edges: the install order (extended tools first) and the PATH order
(extending tools first, so the pinned npm shadows the one node bundles).
One declaration, both consequences, and they cannot drift apart.

**`optional`** marks a capability tool. Nobody downloads a browser engine
until something asks for it. The on-demand path provisions it through
`provision_tool`. Once the facts record it, the normal sweep owns it
like any other tool.

**`missingTargets`** declares a gap with a reason. Only optional tools
can declare gaps. A required tool with a hole bricks the whole
install on that platform, so the table loader refuses it.

Two tools stay off PATH on purpose. `onPath: false` keeps the playwright
browser trees off PATH. A browser binary ahead of the user's own on
PATH is a hijack, not a convenience. Playwright resolves those
trees by directory name, and the store entry names follow playwright's
own spelling.

## The provisioner

`installation/provisioner.py` is the one dependency engine. Three paths
run it:

- `hermes update` runs it as a post-update machine step.
- The installers run `python -m installation.provisioner` after the
  uv bootstrap.
- The desktop payload staging shells out to it. The payload is a
  runtime dir, so it provisions with the same code.

The per-tool flow is fixed:

1. Read the exact pin for this host.
2. Download the artifact.
3. Verify the sha256 BEFORE extraction. A mismatched archive is
   deleted, never unpacked.
4. Stage the tool into a scratch directory.
5. Publish it into the store under `<tool>-<version>-<target>` with one
   atomic rename.
6. Verify by RUNNING the binary. A tool that fails its version probe is
   not recorded.
7. Record the fact.

Two rules make the shared store safe. They are the whole concurrency
story:

- **Publish atomically.** Staging happens in a scratch dir and lands
  with `os.replace`. A reader never sees a half-extracted entry.
- **Never mutate a published entry.** The name carries the version and
  the target, so an existing entry IS the pinned artifact. Another
  install is executing it right now. A pin bump creates a new entry
  and repoints this install's fact at it.

Each published entry carries a marker file, `.hermes-store-entry.json`,
written inside the tree before the rename. The marker records the tool,
version, target, and digest. Its presence is what makes "this entry is
the verified artifact" a fact a later run can check. An unmarked
directory at the same name is junk and is replaced. There is no salvage
and no adoption of unverified bytes.

The staging routines are per-tool but share the fetch-and-verify half.
The interesting ones:

- **npm** installs itself. A plain unpack finds node's bundled npm and
  fails. The provisioner runs node's bundled npm with `--offline` and
  `--global --prefix`, so npm writes the launchers each platform needs.
  The bytes are still the pinned tarball. `--offline` guarantees the
  registry is never consulted.
- **PortableGit** is a self-extracting 7z, not an archive. It must be
  executed to unpack, so the digest check matters more here than
  anywhere else.
- **Playwright browsers** are never flattened. Playwright resolves the
  executable through the archive's own top directory. The provisioner
  also writes playwright's `INSTALLATION_COMPLETE` marker, or the
  registry treats the entry as a partial download and re-fetches.
- **Camoufox** gets a `version.json` written the way camoufox-js reads
  it. The zip does not contain one, and the library raises without it.

Windows publishes with a bounded retry. Defender and the search indexer
scan freshly extracted trees, and a rename fails with access-denied
while any file is held open. PortableGit's thousands of files make git
the reliable loser of that race. The retry backs off for about 15
seconds before giving up. Non-Windows takes the first call.

## System tools

The provisioner prefers a machine-provided tool in one case.

**System git first.** On POSIX, a machine git that clears the flag
floor beats a 147 MB download. The floor is 2.31, and it is derived,
not chosen by taste: `scripts/audit-git-flags.py` extracts every git
argv the codebase builds, and the newest-introduced flag sets the floor.
A system git older than that accepts the probe and then fails
mid-update on a real call. The macOS xcode-select stub is rejected
explicitly. It is not git. It is a dialog launcher. Windows always gets
the managed PortableGit, because bash.exe ships with it and a system
git's bash can be missing.

A system git is recorded with `source: "system"` and an absolute path.
It is never on the managed PATH, and it is never handed tool-specific
env. A fact recorded as system is kept on every later sweep while the
binary exists and still clears the floor.

## Environment assembly

`installation/env.py` is the one place that turns facts into process
environment. Every Hermes-spawned subprocess that must see managed
tools gets its PATH from here. The desktop backend mirrors the same
logic in `apps/desktop/electron/backend-env.ts`. A cross-language test
keeps the two in lockstep.

The assembler prepends the managed tool dirs to PATH in the recorded
order. Tools absent from facts contribute nothing: an unprovisioned
install degrades to system tools instead of shipping dead PATH entries.
System facts are skipped, because promoting `/usr/bin` above the
managed tools hoists everything else that lives there.

The assembler also owns per-tool env:

- `npm_config_cache` points into the install's runtime cache, so
  `~/.npm` stops accumulating install-coupled state.
- `GIT_EXEC_PATH`, `GIT_TEMPLATE_DIR`, `GIT_CONFIG_SYSTEM`,
  `GIT_SSL_CAINFO`, and `PREFIX` point a relocatable git at its own
  tree. These are the same variables dugite's own setup exports.
- `PLAYWRIGHT_BROWSERS_PATH` points at the store, but only when a
  browser fact exists. Otherwise `npx playwright install` runs the user
  does for their own projects keep landing in the default cache.

## The sealed-tree drift check

A Git checkout provisions on demand. Drift there is a normal state that
the next `hermes update` resolves.

A sealed tree cannot self-heal. Its steward built the runtime tools as
part of the artifact, so drift means the artifact was assembled against
a different pin table than the code it ships. `require_current_runtimes`
fails fast in that case: the boot path refuses to limp along with tools
silently missing from PATH. The steward has to rebuild.

## Other consumers of the table

The pin table has four consumers, and they all read the same JSON:

- The Python provisioner, for source installs and `hermes update`.
- The desktop payload staging, which shells out to the provisioner.
- `nix/runtime-pins.nix`, which builds one derivation per tool. The
  `extends` edges become real Nix dependencies, and a `bundle`
  derivation symlinks the tools into the layout the registry describes
  and writes `runtimes.json` with the registry's own code.
- The Docker build. The runtime image's node no longer comes from a
  `node:26` base stage. It comes from the pin table via the
  provisioner. The base stage remains only as a build-time fallback
  for platforms the table does not cover.

`HERMES_RUNTIME_DIR` is the packager's override. A packager builds one
self-contained runtime dir (facts and bytes together) and points the
code at it. The Nix bundle and the desktop payload both work this way.

## Bumping a pin

1. Change the version in `installation/runtime-pins.json`.
2. Update the URL and the sha256 for every target of that tool.
3. Run `scripts/gen-bootstrap-pins.py` to regenerate the bootstrap
   fragment in `setup-hermes.sh`.
4. Run the tests: `scripts/run_tests.sh tests/test_chromium_pin_lockstep.py tests/test_bootstrap_pins_fragment.py tests/installation tests/hermes_cli/test_runtime_facts_cross_language.py`.
5. Verify a provision on this host: `python -m installation.provisioner --json`.
6. Commit the table, the fragment, and any lockfile changes together.

The digest check is the security boundary. Every pin URL must be HTTPS,
except loopback for tests. A plain-http pin lets a network attacker
choose the bytes, and the digest check alone cannot help if the attacker
also picks which digest you compare against.
