import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'

import { test } from 'vitest'

import {
  embeddedRuntimeItems,
  findEmbeddedPython,
  installIdForRoot,
  latestReleaseFromLsRemote,
  PAYLOAD_SCHEMA_VERSION,
  resolvePayload,
  updateChannelFromConfig
} from '../electron/bundled-runtime'

// ─── resolvePayload ────────────────────────────────────────────────

const readerFor = (manifest: unknown) => (p: string) => {
  if (!p.endsWith('manifest.json')) {
    throw new Error('ENOENT')
  }

  return JSON.stringify(manifest)
}

const allDirsExist = () => true
const noDirsExist = () => false

const completeManifest = { schemaVersion: PAYLOAD_SCHEMA_VERSION, tag: 'v1.2.3', commit: 'a'.repeat(40) }

test('resolvePayload returns null for dev runs, external stubs, and garbage', () => {
  assert.equal(resolvePayload(null), null)
  assert.equal(resolvePayload(undefined), null)
  assert.equal(
    resolvePayload('/res', readerFor({ schemaVersion: PAYLOAD_SCHEMA_VERSION, external: true }), allDirsExist),
    null
  )
  assert.equal(
    resolvePayload(
      '/res',
      () => {
        throw new Error('ENOENT')
      },
      allDirsExist
    ),
    null
  )
  assert.equal(resolvePayload('/res', readerFor('not-an-object'), allDirsExist), null)
})

test('resolvePayload rejects old-schema manifests', () => {
  // A schema-2 manifest comes from a pre-embedded artifact. The app and
  // its payload travel together, so a mismatch means a foreign artifact.
  assert.equal(
    resolvePayload('/res', readerFor({ schemaVersion: 2, tag: 'v1.0.0', items: { repo: { status: 'staged' } } }), allDirsExist),
    null
  )
})

test('resolvePayload rejects a payload with a missing item directory', () => {
  // Completeness is a build invariant; a missing directory here means a
  // damaged or truncated artifact.
  assert.equal(resolvePayload('/res', readerFor(completeManifest), noDirsExist), null)

  // One missing item is still a rejection regardless of how many
  // items the platform requires.
  const allButUv = (p: string) => !p.endsWith('/uv')

  assert.equal(resolvePayload('/res', readerFor(completeManifest), allButUv), null)
})

test('resolvePayload returns dir + tag for a complete payload', () => {
  const p = resolvePayload('/res', readerFor(completeManifest), allDirsExist)

  assert.ok(p)
  assert.match(p.dir, /agent-payload$/)
  assert.equal(p.tag, 'v1.2.3')
})

test('the required items include uv — plugin lazy installs are mandatory', () => {
  // A payload without uv cannot lazy-install plugin deps into the
  // writable overlay: incomplete artifact, not a degraded one.
  assert.ok(embeddedRuntimeItems('darwin').includes('uv'))
})

test('every platform requires git and gh in the payload', () => {
  assert.deepEqual([...embeddedRuntimeItems('darwin')].sort(), ['node', 'python', 'repo', 'site-packages', 'uv'])
})

test('the required items include git on Windows', () => {
  assert.deepEqual([...embeddedRuntimeItems('win32')].sort(), ['git', 'node', 'python', 'repo', 'site-packages', 'uv'])
})

test('the required items exclude git on non-Windows', () => {
  assert.ok(!embeddedRuntimeItems('darwin').includes('git'))
  assert.ok(!embeddedRuntimeItems('linux').includes('git'))
})

// ─── findEmbeddedPython ────────────────────────────────────────────

test('findEmbeddedPython picks the patch-versioned dir and needs a real binary', () => {
  const fsStub = (dirs: string[], files: string[]) => ({
    readdirSync: (p: string) => {
      if (!p.endsWith('python')) {
        throw new Error('ENOENT')
      }

      return dirs
    },
    existsSync: (p: string) => files.some(f => p === f)
  })

  // Patch-versioned real dir wins over the minor alias (reverse sort).
  const python = findEmbeddedPython(
    '/res/agent-payload',
    'darwin',
    fsStub(
      ['cpython-3.11-macos-aarch64-none', 'cpython-3.11.15-macos-aarch64-none'],
      ['/res/agent-payload/python/cpython-3.11.15-macos-aarch64-none/bin/python3']
    ) as never
  )

  assert.match(String(python), /3\.11\.15.*bin\/python3$/)

  // No python dir at all → null, not a throw.
  assert.equal(
    findEmbeddedPython('/res/agent-payload', 'darwin', {
      readdirSync: () => {
        throw new Error('ENOENT')
      },
      existsSync: () => false
    } as never),
    null
  )

  // Windows binary lives at the install root, not bin/. The
  // implementation joins with the HOST path module, so the test builds
  // its expected path the same way to stay host-agnostic.
  const winRoot = 'win-res/agent-payload'
  const winExpected = ['win-res/agent-payload', 'python', 'cpython-3.11.15-windows-x86_64-none', 'python.exe'].join('/')

  const winPython = findEmbeddedPython(
    winRoot,
    'win32',
    fsStub(['cpython-3.11.15-windows-x86_64-none'], [winExpected]) as never
  )

  assert.match(String(winPython), /python\.exe$/)
})

// ─── updateChannelFromConfig ───────────────────────────────────────

const ID = 'a4f3b2c1d0e9f8a7'
const record = (channel: string, id: string = ID) => `update:\n  installs:\n    ${id}:\n      path: /home/u/.hermes/hermes-agent\n      channel: ${channel}\n`

test('channel comes from the per-install record; absent means main', () => {
  assert.equal(updateChannelFromConfig(record('stable'), ID), 'stable')
  assert.equal(updateChannelFromConfig(record('"stable"'), ID), 'stable')
  assert.equal(updateChannelFromConfig(record('nightly'), ID), 'nightly')
  assert.equal(updateChannelFromConfig(record('main'), ID), 'main')
  assert.equal(updateChannelFromConfig('model:\n  provider: nous\n', ID), 'main')
  assert.equal(updateChannelFromConfig(null, ID), 'main')
  assert.equal(updateChannelFromConfig('', ID), 'main')
})

test("another install's record never answers for this install", () => {
  // One config.yaml serves many installs — the whole reason the key is
  // per-install. A stable record under a DIFFERENT sha16 must not leak.
  assert.equal(updateChannelFromConfig(record('stable', 'ffffffffffffffff'), ID), 'main')

  // Two records: only ours answers.
  const both = record('stable', 'ffffffffffffffff') + '    ' + ID + ':\n      channel: nightly\n'
  assert.equal(updateChannelFromConfig(both, ID), 'nightly')
})

test('channel parsing stays inside update.installs', () => {
  // A channel key in ANOTHER block must not leak into the answer.
  const text = `gateway:\n  channel: stable\nupdate:\n  interval: 1\nmodel:\n  channel: stable\n`
  assert.equal(updateChannelFromConfig(text, ID), 'main')

  // The update block ends at the next top-level key.
  const ended = `update:\n  interval: 1\nother:\n  installs:\n    ${ID}:\n      channel: stable\n`
  assert.equal(updateChannelFromConfig(ended, ID), 'main')
})

test('installIdForRoot matches boot_bootstrap._install_key (sha16 of the canonical path)', () => {
  // sha256('/home/u/.hermes/hermes-agent')[:16] — recomputed independently.
  assert.equal(installIdForRoot('/home/u/.hermes/hermes-agent'), createHash('sha256').update('/home/u/.hermes/hermes-agent', 'utf8').digest('hex').slice(0, 16))
  // The canonicalizer output is what gets hashed (symlinked homes).
  assert.equal(
    installIdForRoot('/link/hermes-agent', () => '/real/hermes-agent'),
    installIdForRoot('/real/hermes-agent')
  )
})

// ── latestReleaseFromLsRemote ───────────────────────────────────────

test('release picking is numeric, skips prereleases, prefers peeled shas', () => {
  const output = [
    `${'a'.repeat(40)}\trefs/tags/v0.9.0`,
    `${'b'.repeat(40)}\trefs/tags/v0.10.0`,
    `${'c'.repeat(40)}\trefs/tags/v0.10.0^{}`,
    `${'d'.repeat(40)}\trefs/tags/v0.11.0-rc1`,
    `${'e'.repeat(40)}\trefs/tags/v2026.7.20`
  ].join('\n')

  const latest = latestReleaseFromLsRemote(output)

  // v0.10.0 beats v0.9.0 numerically (a lexicographic sort would invert
  // it), the rc prerelease is skipped, and the CalVer tag is excluded by
  // the three-digit major cap — otherwise 2026 would beat every SemVer
  // release forever.
  assert.equal(latest?.tag, 'v0.10.0')
  assert.equal(latest?.sha, 'c'.repeat(40))

  const semverOnly = latestReleaseFromLsRemote(
    [`${'a'.repeat(40)}\trefs/tags/v0.9.0`, `${'b'.repeat(40)}\trefs/tags/v0.10.0`, `${'c'.repeat(40)}\trefs/tags/v0.10.0^{}`].join('\n')
  )

  assert.equal(semverOnly?.tag, 'v0.10.0')
  assert.equal(semverOnly?.sha, 'c'.repeat(40))
})

test('release picking returns null when no final release tag exists', () => {
  assert.equal(latestReleaseFromLsRemote(''), null)
  assert.equal(latestReleaseFromLsRemote(`${'d'.repeat(40)}\trefs/tags/v1.0.0-beta.2`), null)
})
