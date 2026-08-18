import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The spawn is the whole subject here: which interpreter the TUI hands the
// gateway child. Capture the argv instead of starting a real python. The
// stdio are real streams because readline consumes them for real.
const { spawnMock } = vi.hoisted(() => {
  const spawnMock = vi.fn()

  return { spawnMock }
})

vi.mock('node:child_process', () => ({ spawn: spawnMock }))

import { PassThrough } from 'node:stream'

import { GatewayClient } from '../gatewayClient.js'

const fakeChild = () => ({
  pid: 4242,
  killed: false,
  exitCode: null,
  signalCode: null,
  stdout: new PassThrough(),
  stderr: new PassThrough(),
  stdin: new PassThrough(),
  on: vi.fn(),
  once: vi.fn(),
  kill: vi.fn()
})

/**
 * The gateway child must run the interpreter the launcher chose.
 *
 * hermes_cli/main.py::_apply_tui_python_env validates HERMES_PYTHON and
 * falls back to its own sys.executable, and the Nix wrapper sets it too. A
 * TUI started the normal way therefore already knows its interpreter, and
 * scanning the filesystem for a venv here can only find a DIFFERENT python
 * than the parent process runs on — the class of bug where the TUI's gateway
 * imports a different site-packages than the CLI that spawned it.
 */
describe('GatewayClient python resolution', () => {
  const saved: Record<string, string | undefined> = {}
  const managed = ['HERMES_PYTHON', 'PYTHON', 'VIRTUAL_ENV', 'HERMES_TUI_GATEWAY_URL', 'HERMES_TUI_SIDECAR_URL']

  beforeEach(() => {
    for (const key of managed) {
      saved[key] = process.env[key]
      delete process.env[key]
    }

    spawnMock.mockReset()
    spawnMock.mockImplementation(fakeChild)
  })

  afterEach(() => {
    for (const key of managed) {
      if (saved[key] === undefined) {
        delete process.env[key]
      } else {
        process.env[key] = saved[key]
      }
    }
  })

  const spawnedPython = (): string => {
    const call = spawnMock.mock.calls[0] as unknown as [string, string[], unknown]

    return call[0]
  }

  it('spawns the interpreter HERMES_PYTHON names', () => {
    process.env.HERMES_PYTHON = '/opt/hermes/venv/bin/python3'

    new GatewayClient().start()

    expect(spawnedPython()).toBe('/opt/hermes/venv/bin/python3')
  })

  it('prefers HERMES_PYTHON over an activated VIRTUAL_ENV', () => {
    process.env.HERMES_PYTHON = '/opt/hermes/venv/bin/python3'
    process.env.VIRTUAL_ENV = '/some/other/venv'

    new GatewayClient().start()

    expect(spawnedPython()).toBe('/opt/hermes/venv/bin/python3')
  })

  it('ignores VIRTUAL_ENV when the launcher set nothing', () => {
    // A developer's shell venv is not evidence about which python the
    // Hermes install runs on, and the old ladder treated it as if it were.
    process.env.VIRTUAL_ENV = '/some/other/venv'

    new GatewayClient().start()

    expect(spawnedPython()).toBe(process.platform === 'win32' ? 'python' : 'python3')
  })

  it('ignores a bare PYTHON override', () => {
    // PYTHON is a conventional dev/test override, not part of the runtime
    // contract between the launcher and this client.
    process.env.PYTHON = '/usr/bin/python2.7'

    new GatewayClient().start()

    expect(spawnedPython()).toBe(process.platform === 'win32' ? 'python' : 'python3')
  })

  it('falls back to PATH for a bare npm run dev', () => {
    // The one invocation with no launcher above it. The developer runs it
    // inside their own activated environment, so PATH is right there.
    new GatewayClient().start()

    expect(spawnedPython()).toBe(process.platform === 'win32' ? 'python' : 'python3')
  })
})
