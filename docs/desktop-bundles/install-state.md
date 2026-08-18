# Install State

Hermes writes state for one install in four places, and each one answers
a different question. This document names all four, says who writes and
reads each, and records why the count is four rather than the five
anchors it replaced.

Read `runtime-pins.md` for the tool store and `updating.md` for the
update flow. This document is about where the state LIVES.

## The four artifacts

| Artifact | Where | Written by | Answers |
|---|---|---|---|
| Install stamp | `<install root>/install-stamp.json` | The build lane, at build time | What is this artifact, and who applies its next update? |
| Facts | `<runtime dir>/runtimes.json` | The provisioner | Which managed tools does THIS install actually have, and where? |
| Install state folder | `<default home>/installs/<sha16>/` | `boot_bootstrap`, at first touch | What has this install already done, and what has it stored? |
| Channel record | `update.installs.<sha16>` in `config.yaml` | `hermes update --set-channel` | Which releases does this install track? |

Two of the four are keyed by the same `<sha16>`: the sha256 of the
canonical install-root path, truncated to 16 characters
(`boot_bootstrap._install_key`). The key is path-derived on purpose. An
electron-updater update rewrites the artifact at the same path, and both
the state folder and the channel opt-in must survive that.

## The install stamp

`install-stamp.json` is baked in at build time and never edited
afterwards. `installation/tree.py` is the only reader.

Its `updateMechanism` field is required, and `read_build_info` raises
when it is missing rather than guessing. A guessed mechanism misroutes
updates for every install of that artifact, which is worse than a loud
failure at the one build lane that must be fixed. The valid values are
`self`, `electron-updater`, and `external`; an `external` stamp also
names its steward in `distribution`.

`read_build_info` raises on one more thing: a stamp that says
`payload: light`. A light artifact ships no Python runtime, so a Python
process reading its own stamp as light means the artifact was
mispackaged. Failing there beats every consumer misclassifying the tree.

The tree kind is the stamp plus one filesystem fact:

| `.git` present | Stamp mechanism | Tree kind |
|---|---|---|
| Yes | `self` | Managed source checkout. `hermes update` owns it. |
| No | `electron-updater` or `external` | Sealed. Its steward replaces it wholesale. |
| Either | Missing or invalid | Unknown. `read_build_info` raises. |

## The facts

`runtimes.json` lives in the runtime dir and records what this install
provisioned: one entry per tool, with the path to its binary and the
version behind it. The provisioner writes it. Everything that needs a
managed tool reads it through `installation/registry.py` rather than
building a path.

The facts are per install, and the bytes they point at are shared. The
tool store under `~/.hermes/tools/` holds one entry per
`<tool>-<version>-<target>` tuple, so two installs that agree on a pin
share the bytes and two that disagree get one entry each.

The runtime dir is `<install root>/.hermes-runtime` by default.
`HERMES_RUNTIME_DIR` overrides it for packagers that BUILD a runtime dir
instead of provisioning one: the Nix package assembles one from the pin
table at build time, because its install root is an immutable store path
that no provisioner can write to. Treat the location as opaque and go
through the registry. No path literals.

## The install state folder

`<default home>/installs/<sha16>/` holds everything one install
accumulates at runtime:

| Item | Holds |
|---|---|
| `install.json` | The reverse map, sha16 to canonical root, plus the steward. |
| `bootstrap/machine.json` | The machine-scope bootstrap record for this install. |
| `bootstrap/<profile>.json` | One home-scope bootstrap record per profile. |
| `lazy-packages/` | Lazily installed Python packages, for a sealed tree that cannot write its own venv. |

The folder is anchored to the DEFAULT home rather than the active
profile home. Profiles share one folder per install and separate
themselves as files inside it. That keeps profile semantics while the
anchor count stays one.

`install.json` exists to make orphan collection possible. It is the
reverse map, so `hermes doctor` can enumerate `installs/*/install.json`
and flag records whose root no longer exists. It is written once, under
the same single-flight lock the bootstrap records use, and the steward
comes from `runtime_tree` so the record says who owns the tree rather
than who touched it first. A folder without a readable `install.json` is
orphaned by definition: nothing can claim it again, because claiming
goes through `ensure_install_dir`, which writes the record first.

The bootstrap records are an optimization, never the correctness layer.
Every bootstrap step is idempotent, so a deleted record costs one
redundant slow path and nothing more.

## Why four, and not five

Install state used to live in five anchors: bootstrap records in two
different homes, `.hermes-runtime` beside the code, a Docker environment
variable, and the Electron `userData` directory. Five anchors meant five
answers to "where is this install's state", and code that had to know
which one applied to it.

One folder per install key replaced the key-suffixed-file convention.
The sha16 already existed; only the bootstrap records used it. The
runtime dir stayed separate on purpose: it holds bytes that a packager
may build and seal, and the state folder holds what an install writes
while running.

The same consolidation removed a fifth STATE record of a different kind.
`.hermes-install.json` carried an `installMode` field that answered the
same question as the stamp's `updateMechanism`, and the two disagreed.
Two records for one question is the sealed-refusal bug. The manifest is
gone.

## Where the "why two detectors" history lives

An earlier design detected the install kind twice: once by matching the
install root against a table of blessed paths, and once from the stamp.
Path matching cannot survive a moved checkout or a second install, so
the stamp won.

The blessed-root table survives in exactly one place:
`step_adopt_blessed_checkout`, the one-time adoption step that stamps
installs which predate stamping. That step is a migration with an end
condition. When pre-stamp installs are extinct, the step and the last
path table go together. `TODO.md` tracks that sunset.

## Verification

```bash
scripts/run_tests.sh tests/hermes_cli/test_boot_bootstrap.py tests/installation
python -c "
from installation.paths import get_install_root
from installation.tree import runtime_tree
print(runtime_tree(get_install_root()))
"
```
