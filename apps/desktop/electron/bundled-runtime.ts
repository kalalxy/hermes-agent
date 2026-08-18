// bundled-runtime.ts: pure helpers for the embedded desktop runtime.
// An Embedded artifact carries the whole agent runtime in its resources
// and ALWAYS spawns the backend from there — there is no decision contest
// against checkouts. This module only answers: does a complete payload
// exist (resolvePayload), where is its interpreter (findEmbeddedPython),
// and what update channel applies (resolveChannel).
//
// Design: .hermes/plans/2026-08-07_183000-two-axis-install-model.md.
//
// All functions in this file are pure, and the callers inject the
// dependencies. Thus vitest covers the whole decision surface. The impure
// executors live in main.ts and bootstrap-runner.

import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

// ─── payload discovery ──────────────────────────────────────────────────────

export interface PayloadInfo {
  dir: string
  tag: string | null
}

/**
 * Resolve the agent-payload directory that ships in the resources of the
 * packaged app. Returns null for external builds (a stub manifest with
 * external:true), for dev runs (no resourcesPath), for unreadable or
 * old-schema manifests, and for payloads with a missing item directory.
 * Item presence is a build-time invariant (staging fails the build on an
 * incomplete payload), so a missing directory here means a damaged or
 * truncated artifact — the caller reports it, it does not fall back.
 */
export function resolvePayload(
  resourcesPath: string | null | undefined,
  readFile: (p: string) => string = p => fs.readFileSync(p, 'utf8'),
  dirExists: (p: string) => boolean = p => {
    try {
      return fs.statSync(p).isDirectory()
    } catch {
      return false
    }
  }
): PayloadInfo | null {
  if (!resourcesPath) {
    return null
  }

  const dir = path.join(resourcesPath, 'agent-payload')

  let parsed

  try {
    parsed = JSON.parse(readFile(path.join(dir, 'manifest.json')))
  } catch {
    return null
  }

  if (!parsed || typeof parsed !== 'object' || parsed.external === true) {
    return null
  }

  if (parsed.schemaVersion !== PAYLOAD_SCHEMA_VERSION) {
    return null
  }

  if (!embeddedRuntimeItems().every(item => dirExists(path.join(dir, item)))) {
    return null
  }

  return {
    dir,
    tag: typeof parsed.tag === 'string' ? parsed.tag : null
  }
}

// The manifest schema this build understands. Staging writes the same
// number (stage-agent-payloads.mjs); the app and its payload travel in the
// same artifact, so a mismatch means a damaged or foreign artifact.
export const PAYLOAD_SCHEMA_VERSION = 3

// The runtime items inside a complete embedded payload — all of them. uv
// never installs the runtime (site-packages ships prebuilt), but runtime
// lazy installs for plugins are a mandatory feature, and uv is what
// installs them into the writable overlay. A payload without uv is an
// incomplete artifact, not a degraded one. git is Windows-only: macOS has
// /usr/bin/git (Xcode CLT) and Linux has system git, so the staging script
// only downloads PortableGit for win32.
export function embeddedRuntimeItems(platform: NodeJS.Platform = process.platform): readonly string[] {
  const base = ['repo', 'uv', 'python', 'site-packages', 'node'] as const
  return platform === 'win32' ? [...base, 'git'] : base
}

/**
 * Locate the payload CPython binary. The install directory is
 * patch-versioned (python/cpython-3.11.15-<triple>/...), so this scans
 * rather than hardcoding, and it verifies the binary exists.
 */
export function findEmbeddedPython(
  payloadDir: string,
  platform: NodeJS.Platform = process.platform,
  fsImpl: Pick<typeof fs, 'readdirSync' | 'existsSync'> = fs
): string | null {
  const pythonRoot = path.join(payloadDir, 'python')

  let entries: string[]

  try {
    entries = fsImpl.readdirSync(pythonRoot)
  } catch {
    return null
  }

  // Prefer the patch-versioned real directory over the minor alias so the
  // resolved path is stable across launches (the alias is a symlink).
  for (const entry of entries.filter((name) => name.startsWith('cpython-')).sort().reverse()) {
    const candidate =
      platform === 'win32'
        ? path.join(pythonRoot, entry, 'python.exe')
        : path.join(pythonRoot, entry, 'bin', 'python3')

    if (fsImpl.existsSync(candidate)) {
      return candidate
    }
  }

  return null
}

// ─── update channel ─────────────────────────────────────────────────────────

/**
 * The install id of the tree at `root`: sha16 of the canonical PATH,
 * byte-identical to Python's `boot_bootstrap._install_key` (sha256 of the
 * resolved root, first 16 hex chars). Path-derived so it survives artifact
 * replacement at the same location; the same key names `installs/<sha16>/`.
 */
export function installIdForRoot(root: string, canonicalize: (p: string) => string = p => p): string {
  return createHash('sha256').update(canonicalize(root), 'utf8').digest('hex').slice(0, 16)
}

/**
 * The update channel of the install with id `installId`, read from
 * config.yaml text (`update.installs.<sha16>.channel` — the per-install
 * record `hermes update --set-channel` writes; there is no home-global
 * channel key). The CLI owns this shape; Electron only mirrors it for the
 * version pill and the stable-channel check path. Anything but an explicit
 * `stable`/`nightly` record for THIS install means `main` — the default
 * channel for source checkouts.
 *
 * The parser is deliberately narrow: find the top-level `update:` block,
 * the `installs:` block inside it, then the `<installId>:` block, then its
 * `channel:`. config.yaml is machine-written here, so this shape is stable.
 */
export function updateChannelFromConfig(
  configText: string | null | undefined,
  installId: string
): 'stable' | 'main' | 'nightly' {
  if (!configText || !installId) {
    return 'main'
  }

  // Depth by indentation: update: (0) → installs: (>0) → <sha16>: (deeper) →
  // channel: (deeper still). Track the indent at which each block opened so
  // a sibling key at the same depth closes it.
  let updateIndent: number | null = null
  let installsIndent: number | null = null
  let idIndent: number | null = null

  for (const raw of configText.split('\n')) {
    const line = raw.replace(/\s+$/, '')
    if (!line || /^\s*#/.test(line)) {
      continue
    }
    const indent = line.length - line.replace(/^\s+/, '').length
    const key = line.replace(/^\s+/, '')

    if (updateIndent === null) {
      if (/^update:\s*$/.test(line)) {
        updateIndent = indent
      }
      continue
    }
    if (indent <= updateIndent) {
      break // the update block ended
    }
    if (installsIndent === null) {
      if (/^installs:\s*$/.test(key)) {
        installsIndent = indent
      }
      continue
    }
    if (indent <= installsIndent) {
      installsIndent = null
      idIndent = null
      continue
    }
    if (idIndent === null) {
      if (new RegExp(`^${installId}:\\s*$`).test(key)) {
        idIndent = indent
      }
      continue
    }
    if (indent <= idIndent) {
      idIndent = null
      continue
    }
    const match = key.match(/^channel:\s*["']?(stable|main|nightly)["']?\s*(#.*)?$/)
    if (match) {
      return match[1] as 'stable' | 'main' | 'nightly'
    }
  }

  return 'main'
}

/**
 * Pick the newest final release tag (vX.Y.Z, no prerelease suffix) from
 * `git ls-remote --tags` output. Numeric ordering, so v0.10.0 > v0.9.0.
 * Returns null when the output has no final release tag.
 *
 * A peeled entry (`refs/tags/v1.2.3^{}`) resolves the commit that an
 * annotated tag points at. It wins over the unpeeled line of the same tag.
 */
export function latestReleaseFromLsRemote(output: string): { tag: string; sha: string } | null {
  const versions = new Map<string, { key: [number, number, number]; sha: string; peeled: boolean }>()

  for (const line of output.split('\n')) {
    // The major component is capped at three digits: the historical CalVer
    // tags (v2026.7.20) would win every numeric sort. This mirrors
    // _RELEASE_TAG_RE in hermes_cli/update_cmd.py and _SEMVER_TAG_RE in
    // scripts/write_install_stamp.py.
    const m = line.match(/^([0-9a-f]{40})\trefs\/tags\/(v(?:0|[1-9]\d{0,2})\.\d+\.\d+)(\^\{\})?$/)

    if (!m) {
      continue
    }

    const [, sha, tag, peel] = m
    const existing = versions.get(tag)

    if (!existing || (peel && !existing.peeled)) {
      const [major, minor, patch] = tag.slice(1).split('.').map(Number)

      versions.set(tag, { key: [major, minor, patch], sha, peeled: Boolean(peel) })
    }
  }

  let best: { tag: string; sha: string; key: [number, number, number] } | null = null

  for (const [tag, { key, sha }] of versions) {
    const newer =
      !best ||
      key[0] > best.key[0] ||
      (key[0] === best.key[0] && (key[1] > best.key[1] || (key[1] === best.key[1] && key[2] > best.key[2])))

    if (newer) {
      best = { tag, sha, key }
    }
  }

  return best ? { tag: best.tag, sha: best.sha } : null
}
