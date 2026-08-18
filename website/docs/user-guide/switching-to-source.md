---
sidebar_position: 8
title: Switching between the desktop app and a source install
description: Move from the desktop app to a source checkout, or back, without losing data.
---

# Switching between the desktop app and a source install

Hermes runs the same agent from two package shapes:

- **Desktop app** — the agent runs from inside the app's sealed resources.
  The app updates itself (and the agent with it) through its own updater.
- **Source install** — a git checkout that `hermes update` owns, created by
  the install script (or Hermes Setup on Windows).

Your data never lives inside either of them. Chats, config, memory, skills,
and API keys all live in `~/.hermes` (`%USERPROFILE%\.hermes` on Windows),
which sits outside every app container and survives every step on this
page. Uninstalling one shape and installing the other changes *which code
runs*, not what Hermes knows.

## Desktop app → source install

1. **Quit Hermes Desktop.**

2. **Uninstall the app, keeping your data.** The app's own uninstall
   surface (Settings → About) only ever removes user data on a bundled
   install — removing the app itself belongs to your OS:

   - **Windows:** Settings → Apps → Installed apps → Hermes → Uninstall.
     The NSIS uninstaller removes the app only; `%USERPROFILE%\.hermes`
     is untouched.
   - **Windows (Microsoft Store install):** uninstall from the Store or
     from Settings → Apps. Store containers never contain `~/.hermes`.
   - **macOS:** quit the app and drag Hermes.app from Applications to the
     Trash. (`~/.hermes` is in your home directory, not in the .app.)
   - **Linux:** delete the AppImage file (or the app directory) from
     wherever you saved it.

3. **Install from source.**

   - **Linux / macOS:**

     ```bash
     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
     ```

   - **Windows:** download and run
     [Hermes Setup](https://hermes-agent.nousresearch.com/), or use the
     PowerShell one-liner from the website. Both drive the same install
     engine.

   The installer creates the checkout at `~/.hermes/hermes-agent`, stamps
   it as self-updating (`hermes update` owns it from now on), and finds
   your existing data where it always was.

## Source install → desktop app

Four lines:

1. Run `hermes uninstall` (keeps `~/.hermes` unless you pass `--data`).
2. Download the desktop app for your OS from the
   [downloads page](https://hermes-agent.nousresearch.com/).
3. Install and launch it.
4. Done — the app finds your data in `~/.hermes`.

## Which one am I running?

```bash
hermes update --install-id   # prints this install's id and path
hermes version               # prints the install method
```

A sealed desktop tree refuses `hermes update` and points here; a source
checkout updates in place. Channels (`hermes update --set-channel
main|stable|nightly`) are recorded per install, so a desktop app and a
source checkout on one machine track their own channels independently.
