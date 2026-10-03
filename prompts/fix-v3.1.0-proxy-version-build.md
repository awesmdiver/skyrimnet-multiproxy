# Rebuild the v3.1.0 zip with the proxy's version number fixed

**The bug:** the published v3.1.0 release's `proxy.py` still says `PROXY_VERSION = "3.0.0"` (line ~141),
while the v3.1.0 plugin reports 3.1.0. So every player who launches through the plugin sees the
dashboard warning *"Proxy and plugin versions don't match -- you may be running an old copy of one of
them."* Harmless, but a false alarm. `RELEASING.md` covers bumping the plugin's version in two places
but not the proxy's own number.

**Director's decision:** replace the v3.1.0 download in place. Only the version number changes.

## Do this

1. Take `proxy.py` **exactly as it shipped in v3.1.0**: dev repo commit `3c67e60`
   (`git -C ../skyrimnet-multiproxy-dev show 3c67e60:proxy.py`), **not** the dev repo's current `main`,
   which has unreleased work. Change only `PROXY_VERSION = "3.0.0"` to `"3.1.0"`. Diff it against the
   shipped copy: that one line is the only difference.
2. Stage the six files as usual (the other five from the same `3c67e60`), and build
   `github-releases\SkyrimNetMultiProxy-v3.1.0.zip` with `build-release.ps1`. The plugin is unchanged, so
   reuse the existing v3.1.0 plugin build.
3. Check the zip like `RELEASING.md` step 5, and confirm its `proxy.py` says 3.1.0 and matches the shipped
   one apart from that line.
4. **Don't upload it.** The director approves replacing the published file; design side does the upload.
5. **Nothing else for now** (director: "just rebuild 3.1.0 for now"). No build-script check, no
   `RELEASING.md` change, no dev-repo change. Those come later as their own item.

**Handoff:** `prompts/handoff-latest.md` titled exactly
`# Handoff — Rebuild the v3.1.0 zip with the proxy's version number fixed`, with the zip path, the one-line
diff.
