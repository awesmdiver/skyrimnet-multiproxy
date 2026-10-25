# Third-party notices — the Skyrim mod

The Skyrim mod (`SkyrimNetMultiProxy.dll`) is MIT licensed, like the rest of this repository (see `LICENSE`).
It contains no third-party code: it no longer uses CommonLibSSE-NG, and it needs no Address Library.

## SKSE (Skyrim Script Extender)

The mod is loaded by SKSE and talks to it through the plugin interface SKSE documents. The layout of the
few interface structures (`SKSEPluginVersionData`, `PluginInfo`, `SKSEInterface`) in `src/skse_plugin_api.h`
follows the comments in SKSE64's public `PluginAPI.h`; the structures are written out again there and no SKSE
code is linked into the mod.

SKSE is by Ian Patterson, Stephen Abel, Paul Connelly, Adam Rippon and Brendan Borthwick —
https://skse.silverlock.org/ (source: https://github.com/ianpatt/skse64).

## Earlier releases

Earlier mod builds (up to the 4.0.0 Skyrim 1.7 build) were built with CommonLibSSE-NG (MIT for the
CharmedBaryon version, GPL-3.0-or-later with a modding exception for the alandtse version). Their licence
files ship with those builds only.
