# 4.0 release: build the installers and packages from the finished dev code, and publish them

**Held until the dev project's 4.0 queue is finished, including "Full regression before the 4.0 release".**

## Where things live (director, 2026-10-02)

"should installer builds be in dev or public next time? To me, they are customer facing so I would think it's part of
the public release." Agreed split:
- **Dev (`skyrimnet-multiproxy-dev`)** keeps the code that *makes* the installers (`installer/*.iss`, `installer/build.py`,
  `scripts/build_courier_package.py`, the Linux package scripts) and tests them while features are built: test builds
  only, never published.
- **Public (this repo)** owns the **release**: building the shipped files from one tagged dev commit, checking them,
  and publishing them with the release notes. Players only ever get files made here.

## Do this

1. **Pick the release commit:** the dev `main` commit that passed the full regression; tag it `v4.0.0` in the dev repo.
2. **Versions match everywhere** (this replaces the older "make the release build check the proxy's version number"
   item): MultiProxy's own version, the Courier's, both installers' AppVersion, the Skyrim mod's `skse-project.json`
   and the Linux packages all say 4.0.0, and the build refuses to run if any differs (a plain error naming which).
3. **Build from that commit**, on a clean checkout, into this repo's `github-releases/v4.0.0/`: the MultiProxy
   installer, the Courier installer, the Courier zip (for PCs without the installer), the Linux packages, and the Skyrim
   mod package (this repo's own `build-release.ps1`). SHA-256 for every file in a `SHA256SUMS.txt`.
4. **Check them for real** in scratch folders: install, start, the dashboard opens, uninstall leaves nothing unasked;
   the Courier pairs; the version shown everywhere is 4.0.0.
5. **The old do-it-yourself zip route** (this replaces "Release packaging: include the new local-models and Other PCs
   files" and "Setup: offer to install PowerShell 7 instead of stopping"): the installers replace it for players. Say
   in the handoff whether anything of it must stay (the Courier zip for Linux and for PCs without admin rights does);
   don't ship a MultiProxy zip unless something needs it.
6. **Do not publish.** Stop with everything built and checked; the design side writes the release notes and the
   director approves before anything is uploaded or tagged on GitHub.

**Handoff:** `prompts/handoff-latest.md` and `prompts/handoffs/incoming/release-4-0-packaging.md`, titled exactly `# Handoff — 4.0 release: build the installers and packages from the finished dev code, and publish them`.
