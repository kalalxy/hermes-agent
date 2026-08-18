# Install Lifecycle

This document follows one desktop install from first launch to update
and repair. It explains what runs the backend, what decides that, and
which paths exist for each shape.

Read `install-state.md` for where the state lives and `updating.md` for
the update mechanisms. This document is about the sequence.

## One question decides everything: the shape

`installShape()` in `apps/desktop/electron/install-stamp.ts` returns
`bundled` or `checkout`, and every lifecycle decision gates on it.

```ts
export function installShape(stamp = INSTALL_STAMP): 'bundled' | 'checkout' {
  return stamp?.payload === 'bundled' ? 'bundled' : 'checkout'
}
```

The shape comes from the stamp constant baked into the main bundle,
never from a filesystem probe. This matters more than it looks. A
payload, venv, or marker probe answers "is this artifact intact?", which
is a different question from "which shape am I?". Answer the second
question with the first, and a bundled artifact with a damaged payload
quietly becomes a checkout, which then tries to bootstrap a local
install the artifact does not own.

Probes remain, inside an already-chosen shape, as integrity checks. A
bundled stamp with a damaged payload throws.

A dev run has no stamp, and a bootstrap artifact has `payload:
bootstrap`. Both are `checkout`: their runtime is a local install the
app bootstraps and maintains.

## The bundled path: run from the payload

A bundled artifact carries its whole runtime in its resources, and
`createEmbeddedBackend` runs the backend directly out of it. Nothing is
materialized at first launch.

The payload CPython's own site-packages holds `hermes-bundle.pth` with
RELATIVE paths to `repo/` and `site-packages/`, so the interpreter
resolves the runtime from wherever the app sits. The spawn needs no
`PYTHONPATH`, and the install survives renames, Gatekeeper
translocation, and read-only mounts.

An older design materialized the payload into a local checkout at first
launch: copy the tree out, build a venv, run from there. It cost a slow
first launch, it needed somewhere writable, and it created a second copy
of the runtime that could drift from the artifact that shipped it.
Running in place removes all three.

Sealed means read-only, so two environment variables send writes
elsewhere:

| Variable | Sends | Why |
|---|---|---|
| `PYTHONPYCACHEPREFIX` | `__pycache__` writes out of the bundle | A write inside the bundle breaks the code signature, and the mount may be read-only. |
| `HERMES_LAZY_INSTALL_TARGET` | Lazily installed packages to a writable overlay | Same reason, through the existing uv-pip `--target` machinery in `lazy_deps`. |

That overlay lives in the Electron `userData` directory, which is per
install by construction. It used to live under `HERMES_HOME`, and that
was a live collision rather than a theoretical one: lazily installed
wheels are ABI-coupled to the PAYLOAD's CPython, so two installs sharing
one home shared an overlay built for a different interpreter.

## The checkout path: bootstrap a local install

A `checkout` artifact has no payload to run. On first launch it
bootstraps a local install: fetch the source, provision the managed
tools from the pin table, build the venv, then run the backend from
there. `hermes update` owns that tree afterwards.

This is also the shape a dev run takes, which is why a developer's
`npm run dev` behaves like a bootstrap artifact rather than needing a
separate mode.

## Switching to source

A bundled app can point at a source checkout instead of its payload.
The override names the root, and `createEmbeddedBackend` steps aside:
with an override present, the app spawns the backend from that git root
and skips the payload entirely.

The artifact keeps updating itself through electron-updater. The
checkout is the user's, and `hermes update` owns it. There is no eject:
nothing converts the artifact permanently, and no marker records that a
conversion happened. The override is just where the backend comes from.

## Repair is shape-gated

When the backend fails to start, the app can attempt a repair. The
repair ladder escalates on a checkout: soft restart, then reinstall the
venv, then a hard reinstall through the installer.

On a bundled artifact the whole ladder is checkout machinery aimed at a
venv that does not exist. Escalating would run an installer the artifact
does not own. So repair degrades to the soft-restart rung and never
escalates: the sealed payload is replaceable only by electron-updater or
by a reinstall, and the log line says so.

## Adoption: the one-time birth certificate

`step_adopt_blessed_checkout` in `hermes_cli/post_update.py` exists for
installs that predate stamping. A main-era `curl | sh` or Setup install
created a git checkout at a blessed managed root but wrote no stamp.
Under the stamp-only ladder every one of them classifies as somebody's
working tree, and `hermes update` refuses them.

The step writes the missing fact exactly once: blessed root, plus
`.git`, plus no stamp, gives a minimal stamp with `updateMechanism:
self`.

Its guards are what keep it a migration rather than a classifier:

- A `.git` anywhere other than a blessed root is never adopted.
- An existing stamp, whatever it says, is untouched.
- Sealed populations are excluded by construction. Their mechanisms
  replace the tree wholesale with a build-time-stamped one, and sealed
  payloads always ship stamps.
- A read-only tree soft-skips with a debug log rather than crashing.

The blessed-root table lives in this step and only here.
`installation/tree.py` never path-matches. When pre-stamp installs are
extinct, the step and the last path table can be deleted together;
`TODO.md` tracks that sunset.

## Verification

```bash
cd apps/desktop && npx vitest run electron/install-stamp.test.ts
scripts/run_tests.sh tests/hermes_cli/test_post_update_adoption.py tests/installation
```
