# Put the release zip's files inside a "SkyrimNet MultiProxy" folder

Add-on to *Build the v2.3.0 release zip* (already in flight when the director asked for this). Right
now, extracting `SkyrimNetMultiProxy-v2.3.0.zip` dumps every file straight into whatever folder you
extract into. The director wants the zip to contain one top-level folder instead:

```
SkyrimNetMultiProxy-v2.3.0.zip
└── SkyrimNet MultiProxy\
    ├── SkyrimNetMultiProxy.zip
    ├── START HERE.txt
    ├── setup.bat, setup.ps1
    ├── proxy.py, requirements.txt, config.example.json, proxy.ini.example, start-proxy.bat
    └── LICENSE, LICENSE-proxy.txt   (whatever the v2.3.0 build item settled on)
```

**Precondition:** *Build the v2.3.0 release zip* is committed. Check `git log` and read its handoff
first; if it isn't committed, stop and say so.

## Change `build-release.ps1`

- The outer release zip gets a single top-level folder named exactly **`SkyrimNet MultiProxy`** (with
  the space), containing everything that's loose at the top level today.
- **Don't change the inner `SkyrimNetMultiProxy.zip`.** It must keep `SKSE\Plugins\...` at its root —
  that's what makes Vortex/MO2 deploy the DLL into `Data\SKSE\Plugins\` correctly. Only the outer zip
  gets the wrapper folder.
- `Compress-Archive -Path <dir>` (a folder path, not `<dir>\*`) keeps the folder as the archive's root
  entry — or stage into a `SkyrimNet MultiProxy` folder and compress that. Use whichever is cleaner;
  keep the staging area under `$env:TEMP` as today. Watch that the space in the name doesn't break any
  quoting.
- Update the script's header comment to describe the new layout.
- Check `setup.ps1`/`setup.bat` still work when run from inside the extracted folder (they should,
  if they locate files relative to their own location — confirm by reading them, and note if a path
  with a space in it could trip anything).

## Verify

- Run `.\build-release.ps1` → `github-releases\SkyrimNetMultiProxy-v2.3.0.zip` (overwrites the
  earlier build).
- Extract it into an empty scratch folder under
  `F:\Claude Temp Files\skyrimnet-multiproxy\zip-check\` and confirm the result is exactly one folder,
  `SkyrimNet MultiProxy`, holding all the files. List the tree in the handoff.
- Extract the inner `SkyrimNetMultiProxy.zip` too and confirm it still starts at `SKSE\Plugins\`.
- Run `setup.bat` from the extracted `SkyrimNet MultiProxy` folder far enough to confirm it finds its
  files (it's fine to stop before it installs anything; say how far you ran it).

## Commit + handoff

- Ask the director before committing (this project's preference), then commit `build-release.ps1`
  by pathspec and push.
- Handoff: the new zip layout tree, zip size, and the `setup.bat` check result.

End by writing `prompts/handoff-latest.md` with the H1 titled exactly:
`# Handoff — Put the release zip's files inside a "SkyrimNet MultiProxy" folder`
