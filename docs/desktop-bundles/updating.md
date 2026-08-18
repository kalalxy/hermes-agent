# Updating Hermes

This document explains how an install of Hermes updates itself. The
install stamp names the mechanism: a git checkout updates through
`hermes update`, a desktop bundle swaps itself, and a sealed tree
updates through its steward.

## Who updates what

| Install kind | Update path |
|---|---|
| Git checkout | `hermes update` (git pull or tag checkout) |
| Desktop bundle | The in-app updater (electron-updater) |
| Hermes Light | The in-app updater, on the `light` feed |
| Docker | Pull a new image |
| Nix | Update the flake and rebuild |

## What decides the update path

The install stamp decides. Its required `updateMechanism` field names
who owns updates for this install, and every caller asks that one field.

| Mechanism | Who updates | Channel means |
|---|---|---|
| `self` | `hermes update` pulls the git checkout | Which git ref: main, or the newest release tag |
| `electron-updater` | The in-app updater swaps the whole artifact | Which feed: `latest.yml` or `nightly.yml` |
| `external` | A steward replaces the tree: Docker, Nix | Nothing. Channels do not apply |

An earlier design kept a second marker file, `.hermes-install.json`,
with its own `installMode` field. Two records answered one question, and
they disagreed: that is the sealed-refusal bug. The manifest is gone.
The stamp is the only answer, `installation/tree.py` is the only reader,
and there is no eject: `hermes update --eject` and `manageStyle` no
longer exist.

`hermes update` refuses to run where the mechanism is not `self`. The
refusal text names the steward and the correct command. The uninstaller
behaves the same way. These refusals are not cosmetic: a sealed tree
cannot provision itself, and the code must not pretend it can.

## Channels are per install, never per home

`hermes_cli/update_channel.py` records a channel for one install:

```yaml
update:
  installs:
    a4f3b2c1d0e9f8a7:
      path: /home/u/.hermes/hermes-agent
      channel: nightly
```

One `config.yaml` serves many installs. A host checkout, a Docker
gateway, and the desktop app can bind-mount one `~/.hermes`, so a
home-global `update.channel` key is unsafe and does not exist. Selecting
nightly for a dev checkout must not move the desktop app's feed.

The install id is the sha16 of the canonical install-root path, the same
key that names the `installs/<sha16>/` state folder. It is path-derived
on purpose: an electron-updater update writes new stamp bytes at the
same path, and the channel opt-in must survive that.

| Channel | Effect on a `self` install | Effect on a bundle |
|---|---|---|
| `main` | `git pull` of the main branch | Not applicable |
| `stable` | Check out the newest final release tag | The `latest.yml` feed |
| `nightly` | Normalizes to `main`, and the caller prints a note | The `nightly.yml` feed |

An unconfigured install falls to its mechanism default: `main` for a
source checkout, `stable` for a bundle. Nightly builds are release
artifacts, so a git checkout asking for nightly tracks main instead.
`hermes update --set-channel <x>` writes the record from inside the
install, so a user never types a sha. `--install-id` prints it.

Doctor sweeps the records for three staleness kinds: `missing` (nothing
at the recorded path), `replaced` (the path exists but keys to a
different sha16), and `unclaimed` (no live `install.json` for that
sha16). Each one offers garbage collection rather than deleting.

An explicit `--branch` flag on the command line always wins. If a tag
silently overrode that flag, the bug class that `--branch` prevents
comes back: the user said exactly what to track, and the update must
obey.

The stable channel only moves between final releases. Tags are matched
against `vX.Y.Z` with a three-digit-max major, so the legacy CalVer
tags (`v2026.7.20`) can never masquerade as SemVer releases.
Pre-release tags (`v1.2.3-rc1`) are ignored. The newest final tag is
resolved with `git ls-remote --tags origin v*`, so no GitHub API rate
limit applies. When git file I/O is broken, the ZIP-fallback path uses
the GitHub API instead.

## The update flow

`hermes update` runs these phases in order:

1. Pre-update backup (config, skills, state). A snapshot id travels
   through the whole phase so a failure can restore.
2. Stash local changes if the tree is dirty.
3. Fetch and merge (or check out the tag, on the stable channel).
4. Validate the pulled tree: critical files parse, critical modules
   import. A broken tree is refused, not shipped.
5. Run the post-update phase in a FRESH interpreter.
6. Restore the stash, restart the gateway fleet.

The fresh-interpreter detail is load-bearing. The process that pulled
the tree keeps running the code it started with, but the files
underneath it are the newly pulled files. Any module it imports after
that point comes from the new tree. The update phase therefore spawns
`python -m hermes_cli.post_update --update-phase`, and every step in it
imports post-pull code by construction. No reload lists.

## The post-update phase

`hermes_cli/post_update.py` owns the steps. Each step operates on user
state or machine state, never on the install tree. Every step is
idempotent and self-gating: running it twice, or from two installs that
share one `HERMES_HOME`, converges.

The phase has a two-stage entry. `resync_and_reexec` syncs the venv
first, then replaces the process with a fresh interpreter. POSIX uses
`os.execv`, so the same pid keeps the update-lock marker's owner
literally correct. Windows has no true exec, so it spawns and waits.
The `--resumed-after-sync` argv flag is the loop-proofing: the executed
child must not sync again even if another writer moves the stamp
between exec and check, because a flag in argv cannot race. The
venv_sync stamp is the idempotence: a re-run of the whole update sees a
fresh stamp and skips the sync.

`venv_sync` is stdlib-only by contract. It runs on freshly-cloned trees
where the venv does not exist yet, and after tree swaps where the venv
is not trustworthy. On a checkout it runs `uv sync` against the
lockfile, and the lockfile pins a sha256 for every transitive, so a
worm-poisoned release is rejected instead of installed. On a sealed
tree it exits cleanly with `state: sealed`.

The step registries:

| Scope | Steps |
|---|---|
| Home | Config migration, skills sync, state.db integrity guard, launcher repair |
| Machine | cua-driver refresh, runtime provisioning |

The scopes match the records that gate them in the boot bootstrap: home
steps run once per profile per code change, machine steps once per
machine. One step failure never stops the rest, and the caller still
writes its record so a broken step cannot retrigger the slow path on
every boot.

**Launcher repair** (`step_expose_cli`) is new in this restack. The
installers write the POSIX wrapper scripts (`hermes`, `hermes-agent`,
`hermes-acp`) into the link dir exactly once, at install time. A moved
checkout, a recreated venv, or a stray `rm` left stale launchers that
nothing repaired until a full reinstall. The post-update step now
rewrites the wrappers whenever their recorded shape drifts from what
this tree writes today. First-time PATH bootstrapping stays
installer-side on purpose: a boot-time step must not edit rc files on
every update. Windows is a no-op, because the installer already
persists the venv Scripts on User PATH.

## Boot bootstrap

`hermes_cli/boot_bootstrap.py` runs the same post-update steps at boot,
without a user asking. Every install kind compares two per-install
facts:

- Current identity: the stamp commit for sealed trees, `git rev-parse
  HEAD` for checkouts.
- Last-known identity: the commit last bootstrapped, recorded under
  `<home>/install-bootstrap/` keyed by the canonical install root.

Equal means nothing happens, a fast path of about 2 ms. Different means
the idempotent steps run under a single-flight lock, then the new
identity is recorded.

Two records exist, one per step scope. The home record gates home steps
and lives under the active `HERMES_HOME`, so each profile bootstraps
its own state once per code change. The machine record anchors to the
default home, so every profile resolves the same file and machine steps
run once per machine per code change.

The records are an optimization, never the correctness layer. Every
step is idempotent, so a deleted record costs one redundant slow path,
nothing more.

## The old-updater contract

`hermes update` swaps the checkout under its own feet. The process
keeps running the code it started with, but the files underneath are
the newly pulled files. Anything it loads from disk after that point is
a contract with every released updater in the wild. Delete one of those
names and the users on that release get a traceback halfway through an
update, on a tree that is already half-new.

`scripts/audit-old-updater-imports.py` freezes that surface. It walks
everything reachable from the update entrypoints and writes the frozen
set to `tests/compat/old_updater_surface.json`. CI enforces it with
`--check`, so deleting a frozen name fails the build.

The audit over-approximates on purpose. Every miss shrinks the frozen
set, and a symbol wrongly dropped from the set is a bricked update for
whoever reaches that branch. A false positive costs one kept symbol. A
dynamic trace has the opposite bias and is the wrong tool here: one run
takes one path, and it never enters the diverged-history reset, the
Windows rollback, or the ZIP fallback.

Three dynamic patterns matter and are handled explicitly:

- `importlib.reload(m)` re-executes the new file in the old process.
  The most dangerous load in the whole flow, treated as a whole-module
  requirement.
- `getattr(module, "name")` is a symbol requirement with no import
  statement. The Windows venv-holder lookup works this way, and
  silently refuses the update when the name is absent.
- `importlib.import_module(x)` with a non-literal argument cannot be
  resolved statically. It is reported as unresolved rather than
  ignored.

## Windows and POSIX process hygiene

On POSIX, the update tells the user when old code is still running:
after the swap, a notice names the processes that still hold the old
tree, because killing them silently loses their work.

On Windows, the update pauses gateway processes before the swap and
cold-starts them after. It also waits out cron script holders: a cron
job's running script can hold the venv's `.pyd` files, and a rename
over a held file fails. The wait is bounded, and a leftover holder is
reported, not looped on forever.

## Desktop app updates

Bundled and light artifacts update through electron-updater. The
release workflow attaches the feed files (`latest*.yml` for Hermes,
`light*.yml` for Hermes Light) to the GitHub release. The swapped-in
app carries its new runtime in its own resources, so there is no
post-update install step at all.

The gate is `shouldUseAppUpdater`: the stamp mechanism must be
`electron-updater`, and the app must be packaged. Bootstrap artifacts
keep the git path. There is no eject, so no "ejected embedded install"
state exists to gate on.

The update-check result maps onto the existing renderer shape, so the
UI needs no new states. A bundle without a channel record reports
`stable`. Every renderer surface can pick release vocabulary from the
stamp and the channel record, with no second marker file to consult.
The semver verdict comes from electron-updater itself, because a plain
string compare offers a locally-newer dev build a downgrade.

The Linux relaunch path is deliberately honest. After
`hermes update` plus a desktop rebuild, the app only relaunches into
the new GUI when the running binary IS the rebuilt one (execPath under
the rebuilt `release/<platform>-unpacked`). Otherwise it surfaces an
explicit terminal state. Claiming "the new version loads next launch"
produces GUI/backend skew. Before quitting, it preflights the
rebuilt `chrome-sandbox` helper. A fresh rebuild can leave it without
the required setuid mode. A failed relaunch is a dead app.
