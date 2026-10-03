# Build the v2.3.0 release zip

The director wants a **v2.3.0** release build of SkyrimNet MultiProxy to install in Vortex and test.
This item builds the zip locally. **Don't create a git tag, a GitHub release, or upload anything** —
publishing is a separate step after the director has tested it.

Everything from the rename work has landed: the plugin builds as `SkyrimNetMultiProxy.dll`, and the
dev repo (`F:\Claude Workspace\skyrim-modding\skyrimnet-multiproxy-dev`) has the compact dashboard,
optional Claude, Ollama model discovery, readable errors, and the new name.

## 1. Bump the version to 2.3.0

- `skse-project.json` `"Version"` (this is what `build-release.ps1` reads for the zip name)
- `CMakeLists.txt` `VERSION`
- `src/main.cpp`'s startup log line still hardcodes `v2.0.0` — make it report the real version.
  Prefer deriving it from the build (e.g. a CMake-generated define) so it can't drift again; if
  that's awkward with this project's CMake setup, set it to `v2.3.0` and say so in the handoff.
- Any other place a version string lives (grep for `2.2.0`) — list what you changed.

## 2. Refresh release-staging from the dev repo

`build-release.ps1` expects `release-staging\` (gitignored) to hold the proxy files. Replace them with
the current versions from `skyrimnet-multiproxy-dev` at its latest pushed commit (confirm its
`git status` is clean first and note the commit hash in the handoff):
`proxy.py`, `requirements.txt`, `config.example.json`, `proxy.ini.example`, `start-proxy.bat`.
Delete the stale `release-staging\__pycache__`. Never copy `config.json`, `proxy.ini`, `proxy.log`,
`tests/`, or `requirements-dev.txt`.

## 3. Ship the proxy's MIT notices in the zip

The bundled `proxy.py` contains MIT-licensed code from galanx (Claude-SkyrimNet-Proxy) and rhinos0608
(skyrimnet-codex-proxy), and MIT requires their copyright notices to travel with it. The zip currently
only includes the launcher's own `LICENSE` (awesmdiver). Add the dev repo's `LICENSE` to the release
as **`LICENSE-proxy.txt`**: stage it in `release-staging\`, add it to `build-release.ps1`'s
`$externalFiles` list, and update that script's header comment to mention it.

## 4. Build

- Clean Release build of the plugin (fresh `build\` if the cache looks stale).
- Run `.\build-release.ps1` for real this time — output goes to
  `github-releases\SkyrimNetMultiProxy-v2.3.0.zip`.
- List the zip's contents (and the inner `SkyrimNetMultiProxy.zip`'s) in the handoff. Expected
  top level: `SkyrimNetMultiProxy.zip`, `LICENSE`, `LICENSE-proxy.txt`, `setup.bat`, `setup.ps1`,
  `START HERE.txt`, `proxy.py`, `requirements.txt`, `config.example.json`, `proxy.ini.example`,
  `start-proxy.bat`. Inner: `SKSE\Plugins\SkyrimNetMultiProxy.dll` + `.ini`.
- Sanity check the staged `proxy.py`: `python -c "import ast; ast.parse(open(...).read())"`, and
  confirm its dashboard title says `SkyrimNet MultiProxy`.

## Heads-up: docs in the zip aren't final yet

`START HERE.txt` (bundled in the zip) and the launcher's `README.md` still describe the old
Claude-only setup and `ProxyLauncher` file names. The design side is rewriting them in parallel. Don't
edit them here. Once they land, a small follow-up item re-runs `build-release.ps1` so the final zip
includes them. This build is for the director's Vortex install test.

## Commit + handoff

- Ask the director before committing (this project's preference), then commit only the version bump
  and `build-release.ps1` changes by pathspec, and push. `release-staging\` and `github-releases\`
  are gitignored; leave them that way.
- Handoff: the zip's full path and size, its contents listing, the dev repo commit it was built from,
  every version string you changed, and anything that surprised you.

End by writing `prompts/handoff-latest.md` with the H1 titled exactly:
`# Handoff — Build the v2.3.0 release zip`
