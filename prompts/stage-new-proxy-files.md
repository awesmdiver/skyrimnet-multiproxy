# Release packaging: include the new local-models and Other PCs files

The dev repo's proxy is no longer a single file. "Local models, part 1" (dev commit `2df86f2`) added
`local_models.py` and `local_models_ui.py`, and "Local models, part 2" (dev commit `67f29f5`) added
`other_pcs.py`, `other_pcs_ui.py` and `start-proxy.sh`, and "Group long model lists by family" (dev commit
`7888fb0`) added `model_groups_ui.py`, and the graphics memory monitor (dev commit `8e86fb8`) added
`gpu_monitor.py`. `proxy.py` imports the six `.py` files. The release packaging copies only
`proxy.py` (plus the five support files), so the next release zip would ship a proxy that **can't start**.
`RELEASING.md` has been updated (design side); the scripts need to match.

## Do this

1. `sync-release-staging.ps1`: add all seven files to the manifest (~line 66), copied from the dev repo root.
2. `build-release.ps1`: add them to `$externalFiles` (~line 46) and to the file-list comment at the top.
3. **Safety net:** make the build refuse to run if the staged `proxy.py` imports a local module (a
   `.py` file that sits next to it in the dev repo) that isn't staged. So the next new file can't slip
   through the same way. Plain error message naming the missing file.
4. While in there, add the check from the parked item "Make the release build check the proxy's version
   number": refuse to build if the staged `proxy.py`'s `PROXY_VERSION` doesn't equal `skse-project.json`'s
   `Version`. (Say in the handoff that it's covered, so that item can be closed.)
5. Dry run: stage from the dev repo's current `main` and run the build's checks **without** producing or
   publishing a release zip (or build into a scratch folder under `F:\Claude Temp Files\` and delete it).
   Confirm the staged proxy starts (e.g. `py -3 proxy.py --help` or a quick start on a spare port, then stop it).
6. **Two new release assets** that ship alongside the main zip from now on (the dashboard links to
   `.../releases/latest/download/` for both names):
   - `MultiProxy-Model-Server.zip`, built by `python scripts/build_model_server_package.py` in the dev repo.
   - `install-model-server.sh`, the dev repo's `model-server/install-model-server.sh` as a standalone file
     (must keep Linux line endings).
   Make `build-release.ps1` produce both next to the main zip, and add them to `RELEASING.md`'s upload step
   (design side owns RELEASING.md wording, but adding the two file names to its asset list is fine). Dry run
   them too, into scratch, and delete.
7. Commit and push (`git commit -- <your files>`). Don't touch `README.md`.

**Handoff:** `prompts/handoff-latest.md` titled exactly
`# Handoff — Release packaging: include the new local-models and Other PCs files`, with the new error messages verbatim.

Note: more dev work is landing (the MultiProxy Courier will replace the model-server download). Build the file list
from what `proxy.py` actually imports at the time you run, and let the safety net in step 3 catch anything new.
