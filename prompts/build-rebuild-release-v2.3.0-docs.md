# Rebuild the v2.3.0 release zip with the final docs

`START HERE.txt` (bundled in the zip) was rewritten for SkyrimNet MultiProxy after the v2.3.0 zip was
built, so the current `github-releases\SkyrimNetMultiProxy-v2.3.0.zip` still has the old Claude-only
version inside.

**Precondition:** the design side has committed the final `README.md`, `START HERE.txt`, and
`RELEASE_NOTES.md` (check `git log`; the commit touches those files after `61df177`). If not, stop and
say so.

## Do

- Confirm `release-staging\` still matches `skyrimnet-multiproxy-dev`'s latest pushed commit for the
  staged proxy files and `LICENSE-proxy.txt` (re-copy if the dev repo moved on; note the commit).
- Make sure `build\Release\SkyrimNetMultiProxy.dll` is current for HEAD (rebuild if any source
  changed since it was built).
- Run `.\build-release.ps1`.
- Extract the zip into a scratch folder under `F:\Claude Temp Files\skyrimnet-multiproxy\zip-check\`
  and confirm: one `SkyrimNet MultiProxy` folder, the same file list as before, and its
  `START HERE.txt` is byte-identical to the committed one. Delete the scratch folder after.
- **Don't** tag, create a GitHub release, or upload anything.

No code or doc changes are expected, so there's likely nothing to commit. If something did need a
change, ask the director before committing.

Handoff: zip path and size, the dev-repo commit it was built from, and the START HERE check result.

End by writing `prompts/handoff-latest.md` with the H1 titled exactly:
`# Handoff — Rebuild the v2.3.0 release zip with the final docs`
