// stamp-bootstrap-installer-version.mjs — set the real release version on
// Hermes Setup before CI builds it.
//
// The checked-in version is a deliberate 0.0.1 placeholder: Setup has no
// release cadence of its own — it is versioned by the hermes-agent
// release that builds it. CI calls this with --version=<vX.Y.Z tag>
// before `tauri build`, so the exe properties, the NSIS metadata, and
// the Add/Remove Programs entry all carry the release version instead
// of shipping another 0.0.1 artifact.
//
// Touches the three version owners (package.json, tauri.conf.json,
// Cargo.toml [package]) and then verifies the placeholder is GONE from
// all of them — a partial stamp fails the build, never ships.
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const REPO_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const APP = path.join(REPO_ROOT, "apps", "bootstrap-installer")

const arg = process.argv.find((a) => a.startsWith("--version="))
const raw = arg ? arg.slice("--version=".length) : ""
// Accept the tag form (vX.Y.Z / vX.Y.0-nightly.YYYYMMDDHHMMSS) or the bare version.
const version = raw.replace(/^v/, "")
if (!/^\d+\.\d+\.\d+(-nightly\.20\d{6}(?:\d{6})?)?$/.test(version)) {
  console.error(`stamp-bootstrap-installer-version: bad --version=${raw}`)
  process.exit(1)
}

const files = [
  {
    file: path.join(APP, "package.json"),
    stamp: (text) => text.replace(/"version":\s*"0\.0\.1"/, `"version": "${version}"`),
  },
  {
    file: path.join(APP, "src-tauri", "tauri.conf.json"),
    stamp: (text) => text.replace(/"version":\s*"0\.0\.1"/, `"version": "${version}"`),
  },
  {
    // Only the [package] version line — dependency `version = "..."`
    // entries never carry the placeholder, but anchor to be safe.
    file: path.join(APP, "src-tauri", "Cargo.toml"),
    stamp: (text) => text.replace(/^version = "0\.0\.1"$/m, `version = "${version}"`),
  },
]

for (const { file, stamp } of files) {
  const before = fs.readFileSync(file, "utf8")
  const after = stamp(before)
  if (after === before) {
    console.error(`stamp-bootstrap-installer-version: no 0.0.1 placeholder in ${file}`)
    process.exit(1)
  }
  fs.writeFileSync(file, after)
  if (fs.readFileSync(file, "utf8").includes('"0.0.1"')) {
    console.error(`stamp-bootstrap-installer-version: placeholder survived in ${file}`)
    process.exit(1)
  }
  console.log(`stamped ${path.relative(REPO_ROOT, file)} -> ${version}`)
}
