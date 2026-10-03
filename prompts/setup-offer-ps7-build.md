# Setup: offer to install PowerShell 7 instead of stopping

**Director's decision (2026-09-30):** `setup.bat` needs PowerShell 7, which most Windows PCs don't have.
Today it prints "PowerShell 7's pwsh.exe was not found…" and stops. Instead, **offer to install it**,
telling the player it's the latest version of Windows' own command tool.

## Do this

1. In `setup.bat`, when `pwsh.exe` isn't found (both checks it already does), and `winget` is available,
   ask in plain words, e.g.:

   ```
   Setup needs PowerShell 7, the latest version of Windows' own command tool. It's free from Microsoft
   and takes about a minute to install.
   Install it now? [Y/n]
   ```

   Yes: `winget install --id Microsoft.PowerShell -e --accept-package-agreements --accept-source-agreements`,
   then carry on with setup using the newly installed `pwsh.exe` (its default path, since this window's PATH
   won't have refreshed). No, or winget missing, or the install fails: show today's message with the
   download link.
2. Keep it in plain batch (it runs before PowerShell 7 exists). No admin prompt surprises: say before
   installing if Windows might ask for permission.
3. Update `START HERE.txt`'s step 1 to mention it offers to install PowerShell 7 if needed. Report the exact
   final wording of every new line.
4. Test on this PC by pretending pwsh isn't found (e.g. a test switch), **without** actually uninstalling
   anything; say what was and wasn't tested for real.
5. Commit and push (`git commit -- <your files>`). Don't touch `README.md`.

**Handoff:** `prompts/handoff-latest.md` titled exactly
`# Handoff — Setup: offer to install PowerShell 7 instead of stopping`, with every new line verbatim.
