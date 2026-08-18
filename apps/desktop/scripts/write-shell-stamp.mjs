// write-shell-stamp.mjs — the app shell's own install stamp (build/).
//
// The ONE knob is HERMES_PAYLOAD_UPDATE_MECHANISM: the win32 release
// build packs twice top-down — an electron-updater pass (nsis) and an
// `external` pass (msix, store-managed) — and each pass regenerates BOTH
// stamps (this shell stamp and the payload repo stamp in
// stage-agent-payloads.mjs) through the canonical writer. Nothing ever
// mutates a stamp after it is written.
//
// uv run, not bare python3: on Windows `python3` resolves to the
// Microsoft Store alias (exit 9009); uv is a hard prerequisite of the
// desktop build anyway.
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const repoRoot = path.dirname(path.dirname(desktopRoot))

const mechanism = process.env.HERMES_PAYLOAD_UPDATE_MECHANISM || 'electron-updater'

const result = spawnSync(
  'uv',
  [
    'run',
    '--no-project',
    path.join(repoRoot, 'scripts', 'write_install_stamp.py'),
    '--output',
    path.join(desktopRoot, 'build', 'install-stamp.json'),
    '--update-mechanism',
    mechanism
  ],
  { stdio: 'inherit', shell: process.platform === 'win32' }
)

process.exit(result.status ?? 1)
