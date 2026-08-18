// app-updater.ts — electron-updater integration for bundled desktop installs.
//
// Bundled installs update through GitHub Releases: electron-updater reads
// latest*.yml from the release that the desktop-bundled-release workflow
// attached, downloads the new installer, and applies it. The swapped-in app
// carries the new runtime in its own resources (embedded mode), so there is
// no post-update install step at all.
//
// Source installs never reach this module. The callers gate on the install
// manifest first and fall through to the git-based update path.
//
// The decision helpers are pure so vitest covers them. The impure wrapper
// at the bottom lazy-loads electron-updater, because the module must not
// cost anything on thin builds.

import type { AppUpdater } from 'electron-updater'

import type { ArtifactKind } from './install-stamp'
import { PRODUCT_IDENTITY } from './product-identity'

export interface UpdaterGateFacts {
  stampPayload: ArtifactKind
  isPackaged: boolean
}

/**
 * True when this launch must use electron-updater for app updates.
 *
 * Both conditions are necessary:
 * - the artifact kind self-updates through the release feed: 'bundled'
 *   and 'light' both do ('bootstrap' artifacts have no matching feed
 *   artifacts and keep the git path),
 * - the app is packaged (dev runs have no app-update.yml).
 *
 * This is a constant of the artifact, not of machine state. An eject
 * replaces the whole app with a source-built external one (payload
 * 'bootstrap'), so no "ejected embedded install" state exists to gate on.
 */
export function shouldUseAppUpdater(facts: UpdaterGateFacts): boolean {
  return (facts.stampPayload === 'bundled' || facts.stampPayload === 'light') && facts.isPackaged === true
}

/**
 * Map an electron-updater check result to the renderer's update-check shape
 * (the shape hermes:updates:check already returns for the git path). The
 * renderer then needs no new states: `updateAvailable` plus `mechanism`
 * drive the existing UI.
 */
export function describeFeedCheck(
  current: string,
  info: { version?: string } | null | undefined,
  isUpdateAvailable?: boolean,
  channel: 'stable' | 'nightly' = 'stable'
): {
  supported: true
  mechanism: 'app-updater'
  channel: 'stable' | 'nightly'
  currentVersion: string
  latestVersion: string | null
  latestTag: string | null
  updateAvailable: boolean
  fetchedAt: number
} {
  const latest = info && typeof info.version === 'string' ? info.version : null

  return {
    supported: true,
    mechanism: 'app-updater',
    // The channel this bundled install tracks: stable (latest.yml) unless
    // its per-install record opts into nightly (nightly.yml). Saying so
    // here lets every renderer surface pick release vocabulary without a
    // separate probe.
    channel,
    currentVersion: current,
    latestVersion: latest,
    latestTag: latest ? `v${latest}` : null,
    // Prefer electron-updater's own semver verdict: a plain string compare
    // would offer a locally-newer dev build a downgrade.
    updateAvailable: isUpdateAvailable ?? (latest !== null && latest !== current),
    fetchedAt: Date.now()
  }
}

// ── impure wrapper ──────────────────────────────────────────────────────────

let cachedUpdater: AppUpdater | null = null

/**
 * Lazy singleton for electron-updater's autoUpdater. The require sits inside
 * the function so thin builds and tests never pay for the module load.
 * autoDownload stays off: the renderer asks the user before the download
 * starts (same consent model as the git path).
 * autoInstallOnAppQuit stays off too: a quit-time install would skip the
 * pre-install backend teardown in applyAppUpdate, and on Windows a
 * surviving backend grandchild keeps files in the install directory
 * locked while the installer replaces it. Installs happen only through
 * applyAppUpdate.
 */
export function getAutoUpdater(): AppUpdater {
  if (cachedUpdater) {
    return cachedUpdater
  }

  const { autoUpdater } = require('electron-updater') as { autoUpdater: AppUpdater }

  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false
  cachedUpdater = autoUpdater

  return autoUpdater
}

/** Check the GitHub Releases feed. Returns the renderer-shaped result. */
export async function checkAppUpdate(
  currentVersion: string,
  channel: 'stable' | 'nightly' = 'stable'
): Promise<ReturnType<typeof describeFeedCheck>> {
  const updater = getAutoUpdater()

  // electron-updater's channel property selects which <channel>.yml the
  // check reads. Set it on every check: the user can flip the per-install
  // record between two checks of one app session. The nightly feed name is
  // per-variant (nightly.yml for Hermes, light-nightly.yml for Light);
  // null restores the baked default (latest.yml / light.yml — whatever
  // app-update.yml says). allowPrerelease rides along — nightly artifacts
  // publish as GitHub prereleases.
  updater.channel =
    channel === 'nightly' ? (PRODUCT_IDENTITY.light ? 'light-nightly' : 'nightly') : null
  updater.allowPrerelease = channel === 'nightly'

  const result = await updater.checkForUpdates()

  return describeFeedCheck(currentVersion, result?.updateInfo, result?.isUpdateAvailable, channel)
}

/**
 * Download the update, then quit and install. `onProgress` receives percent
 * values from electron-updater's download events. `beforeInstall` runs after
 * the download completes and before quitAndInstall — the caller uses it for
 * backend teardown that must happen while the process is still alive (on
 * Windows a surviving backend grandchild keeps files in the install
 * directory locked while the installer replaces it). The returned promise
 * resolves after the download; quitAndInstall exits the process.
 *
 * `updater` is injectable so vitest can assert the ordering contract
 * (download → beforeInstall → quitAndInstall) without electron-updater.
 */
export async function applyAppUpdate(
  onProgress?: (percent: number) => void,
  beforeInstall?: () => void | Promise<void>,
  updater: AppUpdater = getAutoUpdater()
): Promise<{ ok: true }> {
  const handler = onProgress ? (p: { percent: number }) => onProgress(p.percent) : null

  if (handler) {
    updater.on('download-progress', handler)
  }

  // The listener must come off on failure too: the updater is a process-wide
  // singleton, and a retry after a failed download would stack a second
  // listener that fires ghost progress events.
  try {
    await updater.downloadUpdate()
  } finally {
    if (handler) {
      updater.removeListener('download-progress', handler)
    }
  }

  if (beforeInstall) {
    await beforeInstall()
  }

  updater.quitAndInstall()

  return { ok: true }
}
