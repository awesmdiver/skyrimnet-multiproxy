# Plugin: start MultiProxy outside the game, and tell it when Skyrim starts and closes

**Spec:** `docs/plans/2026-10-01-installers-and-tray-spec.md` (in `skyrimnet-multiproxy-dev`; read all of it, including "From the Gemini design review"), **Part 5** and "From the Gemini design review". The dev-repo item "MultiProxy in the tray: no
terminal window, a status dot, start/stop and logs" defines the contract this builds against (its `TECHNICAL.md`
section and the pointer file `%APPDATA%\SkyrimNet MultiProxy\install-info.json`): read that first.

## Do this (the SKSE plugin, C++, in this repo)

1. On game start: read the pointer file. **Not there** (plugin installed through a mod manager, installer never run):
   fall back to today's behaviour if the old proxy files are beside the plugin, otherwise stay quiet (one line in the
   plugin's own log, nothing in game).
2. Ping the running MultiProxy. If it doesn't answer, start the tray app from the pointer file, **hidden and outside
   the mod manager's job** (`CREATE_BREAKAWAY_FROM_JOB`, with a fallback when the job forbids breakaway, e.g. having
   Explorer's shell start it), so Mod Organizer 2 doesn't think Skyrim is still running after the game closes.
3. Tell it "Skyrim started" with the game folder; on game exit tell it "Skyrim closed". **Never kill a process.**
   The tray app decides whether to quit (the "Start and stop with Skyrim" setting).
4. Version numbers stay in step (`PROXY_VERSION` in the dev repo, `skse-project.json`, `CMakeLists.txt`).
5. Build a plugin-only package `SkyrimNetMultiProxy-Mod-<version>.zip` (just the plugin, laid out for mod managers)
   in the build output; the Windows installer item picks it up.
6. **Prove the risky part for real** with a stand-in program if the tray app isn't built yet: launch under **Mod
   Organizer 2** and under **Vortex**, close the game, and confirm the mod manager unlocks while the started program
   keeps running. If you can't run the game here, say exactly what the director needs to do to check it, and build a
   small test harness that does the same launch from inside a job object so the breakaway is at least proven that way.

## Also: the files were renamed

In the dev repo `proxy.py` is now `multiproxy.py` and `proxy.ini` is `multiproxy.ini` (a small `proxy.py` shim stays for
old setups). Update the plugin's defaults, the sample `SkyrimNetMultiProxy.ini`, the release scripts, `release-staging`
and `RELEASING.md` to the new names; the plugin must still start an old install that only has `proxy.py`.


**Wording (added 2026-10-01): it is a "mod", never a "plugin", in anything a player reads.** The director: "our piece for the mod manager is not a plugin, it's a Mod." Say "the MultiProxy mod" / "Skyrim mod" / "Mod not detected" in every player-facing string, including existing ones you touch (list the ones you changed in the handoff, and flag any you saw but didn't own). The package is `SkyrimNetMultiProxy-Mod-<version>.zip`. "SKSE plugin" stays fine in code, comments and `TECHNICAL.md`.

## Wrap-up

- The plugin builds clean. Don't touch `README.md`. Commit and push (`git commit -- <your files>`). Publish nothing.
- **Handoff:** `prompts/handoff-latest.md` titled exactly `# Handoff — Plugin: start MultiProxy outside the game, and tell it when Skyrim starts and closes`.
