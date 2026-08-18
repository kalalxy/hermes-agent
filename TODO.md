# TODO

Work the desktop-bundle restack identified and deliberately did not do.
Each item says what to do, why it waited, and where the evidence is.

An item belongs here when it is real work with a known trigger. An item
that needs a decision first belongs in a plan document or an issue.

## Test failures to investigate

**1. Four CI-shaped failures.** These failed in branch CI and pass
locally on macOS: `test_google_chat_oauth_dependencies.py`,
`test_turn_lease.py`, `test_compression_concurrent_fork.py`,
`test_zombie_process_cleanup.py`. Start with the google_chat one: its
subject is optional oauth dependencies, so a CI runner without those
extras installed is the first hypothesis to test. The other three are
concurrency-shaped and are more likely to be timing.

**2. Two pre-existing failures on this branch.** Each one was proven
pre-existing by stashing the working tree and re-running, so neither
comes from the restack. Neither is in the handoff's known-red list
either. Both fail on Linux today:

| Test | Subject |
|---|---|
| `tests/test_windows_subprocess_no_window_flags.py::test_lazy_deps_uv_install_hides_console_window` | The lazy-deps uv install path does not pass the no-window flags the test expects. |
| `tests/agent/lsp/test_install_and_lint_fixes.py::test_install_npm_works_without_extras` | An LSP install path that expects npm to work without extras. |

**3. `audit-old-updater-imports.py --check` exits 1.** Four names the
frozen surface expects are not found:
`hermes_cli.gateway.GATEWAY_LOOP_WEDGED`,
`hermes_cli.gateway._escalate_wedged_gateway`,
`hermes_cli.gateway.probe_gateway_loop_liveness`, and
`hermes_constants.DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT`, all reached
from `hermes_cli/update_cmd.py:_cmd_update_impl`.

Pre-existing, and proven so causally rather than by inspection: a
throwaway worktree at `ceb55cbbb`, the commit before any of the
documentation work, fails with the same four names. Either the frozen
set in `tests/compat/old_updater_surface.json` needs regenerating, or
those four names were renamed without updating it. The audit is a
release gate, so resolve it before the branch ships.

**4. lazy_deps: five pre-existing failures.** Verify whether
`ae2db7da2` ("read the lazy-install specs from the pyproject extras")
resolved them. The commit is in the series, so this is a re-run rather
than an investigation.

## Sunsets with a trigger

**5. The Docker lazy-overlay bridge.** `tools/lazy_deps.py` keeps
`_LAZY_TARGET_ENV` as an explicit override for one release, because
Docker's `/opt/data` anchor predates the per-install state folder. The
desktop half of the same bridge dies with the Electron work. File a
tracking issue so the Docker half is removed on schedule rather than
becoming permanent.

**6. `step_adopt_blessed_checkout`.** The step writes a birth
certificate for installs that predate stamping, and it holds the last
path-matching table in the codebase. When pre-stamp installs are
extinct, delete the step and the table together. See
`docs/desktop-bundles/install-lifecycle.md`.

**7. `HERMES_NODE`.** The Nix wrapper sets it
(`nix/hermes-agent.nix:269`), and `nix/checks.nix` asserts it. Now that
node resolves through the runtime facts, the variable can become a
system-source fact instead. Removing it means teaching the Nix bundle to
record a fact for the node it already builds.

## Decisions to revisit

**8. The pins `python` field is uv-only.** `installation/registry.py`
rejects a `python` pin on any tool except uv, because uv is the
installer that pin configures. Revisit when a second tool needs to pin
an interpreter.

**9. `psutil_android` compat surface.** Only
`tests/hermes_cli/test_psutil_android_extract.py` exercises
`hermes_cli.psutil_android`. Decide whether the module stays after the
Termux removal, and remove it with its test if not. See
`docs/termux-removal-notes.md` for the revival checklist.

**10. PR #2 re-audit.** Measure whether `Lib/venv` plus pip and ensurepip
are prunable now that Phase 7 landed and eject is gone. Record the extra
megabytes the prune saves. This gates PR #2, which is why that PR folds
last.

**11. Nightly notarization minutes.** macOS notarization on every
nightly costs runner minutes. If the cost becomes real, gate the nightly
lane behind `workflow_dispatch` rather than dropping notarization.

**12. MSIX and `app-update.yml`.** Verify whether the file ships inside
the MSIX resources. electron-updater does not update MSIX installs, and
the stamp's mechanism field is the real gate, so a shipped
`app-update.yml` there is a decoy that will mislead the next reader.
Check the first CI-built MSIX artifact.

## Code hygiene

**13. Plan-document references in shipped source.** Five modules
introduced by this branch cite a design document under `.hermes/plans/`,
and none of those documents ships in the tree:

| Module | Cites |
|---|---|
| `installation/registry.py:38` | `2026-08-12_hermes-home-lifetime-split.md` |
| `installation/env.py:13` | `2026-08-12_hermes-home-lifetime-split.md` |
| `hermes_cli/boot_bootstrap.py:31` | `2026-08-10_163500-boot-time-post-update-bootstrap.md` |
| `hermes_cli/post_update.py:16` | `2026-08-10_163500-boot-time-post-update-bootstrap.md` |
| `apps/desktop/scripts/stage-agent-payloads.mjs:4` | `2026-08-07_resources-resident-bundled-runtime.md` |

A reader who follows one of those paths finds nothing. `tools/lazy_deps.py`
has the same problem in a different spelling: its resolution order cites
"doc4 §B". Point each citation at the document that DOES ship, under
`docs/desktop-bundles/`, or state the reasoning in the docstring itself.
Plan documents are working notes; the docs directory is the home.

Older main-era modules (`gateway/authz_mixin.py`,
`agent/turn_finalizer.py`, `hermes_cli/model_setup_flows.py`,
`hermes_cli/cli_agent_setup_mixin.py`) cite a
`god-file-decomposition.md` that does not ship either. Same fix, wider
sweep, and not this branch's work to do.

**14. The `diag-*.mjs` inventory.** `apps/desktop/scripts/` holds 16
diagnostic scripts (drag churn, overlay sweeps, switch autopsy, key
latency, and more). Each was written for one investigation. Document
which ones are still useful and delete the rest. An undocumented
diagnostic script is a file the next reader has to open to classify.

**15. Windows `step_expose_cli` is a no-op.** The Windows installer
already persists the venv `Scripts` directory on the user PATH, so the
launcher-repair step does nothing there. The signed-trampoline
workstream is what would give Windows the same repair the POSIX wrappers
get.

## Tooling

**16. Port `check-windows-footguns.py` to an AST visitor.** Keep the
purely lexical rules (wmic, `~/Desktop`) as regex, keep the Footgun
registry and the `# windows-footgun: ok` marker, and match the marker by
node line number. Move the Python-semantic rules to `ast.NodeVisitor`:
`open`/`fdopen`/`read_text`/`write_text` encodings, `text=True`, and
`os.kill` sites.

Evidence, from the P5 sweep on 2026-08-18: the line regex read a nested
call's argument as a mode string, then classified `Path.open("a")` as a
read, because the builtin takes the mode second and the method takes it
first. It rewrote 18 append sites to `utf-8-sig`, which writes a BOM on
every append. `test_lifecycle_ledger` caught it.

An AST gets comments, docstrings, multi-line calls, and keyword-argument
modes for free, and deletes roughly 150 lines of parser heuristics
(`_strip_code`, the triple-quote state machine, `_call_closes_on_line`,
`_OPEN_MODE_RES`). Receiver typing stays heuristic either way, because
`x.open()` can be `tarfile`. The same builtin-shape restriction applies,
structurally rather than textually.

## Rebase

**17. The P11 fixup target is unresolved.** The cleanup plan named
`95f91f2f7` and `6180ab993` as fixup targets for the pins-schema work.
Neither can take it: at `95f91f2f7` the schema sits at the repo root and
the registry is `hermes_cli/runtime_registry.py`, and neither
`nix/runtime-pins.nix` nor the doc page exists yet. The earliest commit
where every touched path exists in its current place is `5d04d0d5e`, the
rename itself. The change landed as its own commit, and its message
carries the same note. Resolve during the rebase.
