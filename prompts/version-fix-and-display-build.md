# Fix the plugin's compiled version string + show both versions on the dashboard

Queued 2026-09-20, two related pieces for the v3.0.0 release currently being finalized.

## Part A — The compiled plugin still reports v2.5.0 (real bug, fix and rebuild)

Found live: `SkyrimNetMultiProxy.log` reports v2.5.0 even in the newly-built v3.0.0 plugin.
`skse-project.json`'s `Version` field (used only for the release zip's own naming/packaging) was
already bumped to `3.0.0`, but `CMakeLists.txt`'s `project(SkyrimNetMultiProxy VERSION 2.5.0 ...)`
— the thing that actually feeds `PROJECT_VERSION` → `SKYRIMNET_MULTIPROXY_VERSION_STRING` (see the
comment right above that `target_compile_definitions` block) — was never bumped. That string is what
`SKSE::log::info` prints at startup and what `SKSEPluginInfo`'s own version reporting uses.

**Already fixed in the working tree**: `CMakeLists.txt` line 8 now reads `VERSION 3.0.0`. This still
needs a real reconfigure + rebuild — a `project()` version change requires CMake to regenerate, not
just `cmake --build` against the existing cache. Use the documented commands (`TECHNICAL.md`):

```
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DCMAKE_SKIP_INSTALL_RULES=ON
cmake --build build --config Release
```

After building: confirm `SkyrimNetMultiProxy.log` (or the harness's own `test_launch.exe`, which
prints whatever `SKSEPluginLoad` would) actually reports `v3.0.0.0` this time — that's the whole
point of this fix, verify it directly rather than trusting the version number alone. Deploy the
rebuilt `.dll` to the live install (`Data/SKSE/Plugins/SkyrimNetMultiProxy.dll`) the same way the
previous session's own build did — confirm Skyrim is closed first. Then rebuild the release zip
(`.\build-release.ps1`, after re-running `.\sync-release-staging.ps1` if `proxy.py` has changed —
check `git status` in `skyrimnet-multiproxy-dev` first) so the shipped zip actually contains the
correctly-versioned binary.

## Part B — Show both versions on the dashboard, so this class of mismatch is never silent again

This exact bug (a version bumped in one place but not the other) is worth making impossible to miss
in the future — surface both the proxy's own version and the plugin's version right on the dashboard,
and flag it visibly if they ever disagree.

**`skyrimnet-multiproxy-dev` (`proxy.py`)**:
- Add a `PROXY_VERSION = "3.0.0"` constant near the top of the file (bumped by hand each release,
  same manual-bump pattern `skse-project.json`'s own `Version` field already follows — no attempt at
  deriving it automatically).
- Accept an optional CLI argument (e.g. `--plugin-version=X.Y.Z`) that, when present, records the
  plugin's version proxy.py was told about. When launched without it (manual `py proxy.py`,
  `start-proxy.bat`, or any path that doesn't go through the SKSE plugin), this is simply absent —
  handle that as a normal, expected case, not an error.
- Show both on the dashboard's existing Setup card, in the same `grid3` stats row as `Template` /
  `Default model` / `Claude CLI` (~proxy.py:5544) — add a fourth stat, or restructure to a `grid4` if
  that reads better with four items; use your judgment on the cleanest layout, but keep it visually
  consistent with the existing three. Show something like "Proxy 3.0.0" and "Plugin 3.0.0" (or
  "Plugin: not detected" when launched without the plugin). **If both are known and they don't
  match, make that visually obvious** — the mockup precedent elsewhere in this dashboard for a
  "something's off" state is the `warn` color/dot already used for verdicts and provider status
  (yellow, not red — a mismatch is worth noticing, not alarming). A short plain-language note near it
  (e.g. "proxy and plugin versions don't match — you may be running an old copy of one of them") is
  more useful than just showing two numbers and letting the player do the math themselves.

**`skyrimnet-multiproxy` (this repo, `src/proxy_launcher.cpp`)**:
- When building the command line to launch `proxy.py` (~`LaunchProxy()`, the
  `cmdLine = "\"" + pythonExe + ...` construction), append the plugin's own version as that new CLI
  argument, e.g. `--plugin-version=3.0.0` — reuse whatever `PROJECT_VERSION`/version-string constant
  the plugin itself already has access to (the same one Part A's fix makes correct), don't hardcode a
  second copy of the version number.

Keep this additive and low-risk: a proxy launched without the new argument (any pre-existing manual
setup) must keep working exactly as it does today — the plugin-version display simply reads as
unknown/not-detected in that case, nothing breaks or warns falsely.

## Tests

- `skyrimnet-multiproxy-dev`: a test confirming the dashboard shows `PROXY_VERSION`; a test confirming
  the plugin-version argument is parsed and displayed when present, and gracefully absent when it
  isn't; a test confirming the mismatch note appears only when both versions are known AND actually
  differ (not when one is unknown).
- This repo: if there's a reasonable way to verify the new `--plugin-version=...` argument is now
  present in the constructed command line (the existing `tools/launch-proxy-test` harness may be a
  good place to check this, or a simpler unit-level check if `LaunchProxy()`'s command-line
  construction can be tested in isolation) — use your judgment on how deep to test C++ string
  construction here, this doesn't need the same rigor as Part A's actual startup-detection logic.

## Verification

- Both repos: full test suites green.
- Live: confirm `SkyrimNetMultiProxy.log` reports `v3.0.0.0` (Part A), and confirm the dashboard
  shows both versions matching, with no false mismatch warning, on a real launch through the actual
  SKSE plugin (Part B).

When verification passes in each repo, commit + push (narrow `git add`, only the files you actually
changed) and write the handoff — one handoff per repo if both need real changes, since they're
separate git histories.

**Handoff:** title each exactly `# Handoff — Fix the plugin's compiled version string + show both versions on the dashboard`.
