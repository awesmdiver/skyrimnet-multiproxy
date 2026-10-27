"""Finding the newest numbered llama.cpp build and the files it holds for one kind of engine.

GitHub's "latest" llama.cpp release is NOT a build: it is a stub (`v0.6.0`, one `nightly-tag.txt` file). The real builds are tagged `bNNNNN`, and every one of them is
marked "pre-release" on GitHub (checked 2026-10-06), so a filter on "not a pre-release" would pick the stub too. The newest build is therefore chosen by the NUMBER in its
tag, from the list of releases, never from the "latest" pointer and never by the pre-release flag.

Every file is checked twice before it is used: here, that the release lists its size and SHA-256 (GitHub's `digest`); and when it is downloaded (`local_models.download_file`),
that the bytes match them. A file whose digest is not published is refused, never guessed. Nothing here installs anything: `LocalModels.update_engine` does that, and only
when the player presses Update.
"""
from __future__ import annotations

import re
from typing import Optional

RELEASES_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"
BUILD_TAG = re.compile(r"^b(\d+)$")
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")

# What each kind of engine is called in a release, per system. `{b}` is the build tag. The NVIDIA runtime libraries (`cudart`) are separate downloads: on Windows they
# carry no build number (the same file in every release), on Linux they do. ROCm's number (`10.0`) changes with the release, so it is matched, not spelled.
WINDOWS = {
    "cpu": [r"llama-{b}-bin-win-cpu-x64\.zip"],
    "vulkan": [r"llama-{b}-bin-win-vulkan-x64\.zip"],
    "cuda-13.4": [r"llama-{b}-bin-win-cuda-13\.4-x64\.zip", r"cudart-llama-bin-win-cuda-13\.4-x64\.zip"],
    "cuda-12.4": [r"llama-{b}-bin-win-cuda-12\.4-x64\.zip", r"cudart-llama-bin-win-cuda-12\.4-x64\.zip"],
    "rocm": [r"llama-{b}-bin-win-rocm-[0-9.]+-x64\.zip"],       # carries AMD's own runtime (amdhip64, comgr): needs only a recent AMD driver
    "sycl": [r"llama-{b}-bin-win-sycl-x64\.zip"],               # carries Intel's runtime (sycl, mkl, level-zero): needs only a recent Intel graphics driver
}
LINUX = {
    "cpu": [r"llama-{b}-bin-ubuntu-x64\.tar\.gz"],
    "vulkan": [r"llama-{b}-bin-ubuntu-vulkan-x64\.tar\.gz"],
    "cuda-13.4": [r"llama-{b}-bin-ubuntu-cuda-13\.4-x64\.tar\.gz", r"cudart-llama-{b}-bin-ubuntu-cuda-13\.4-x64\.tar\.gz"],
    "cuda-12.8": [r"llama-{b}-bin-ubuntu-cuda-12\.8-x64\.tar\.gz", r"cudart-llama-{b}-bin-ubuntu-cuda-12\.8-x64\.tar\.gz"],
    "rocm": [r"llama-{b}-bin-ubuntu-rocm-[0-9.]+-x64\.tar\.gz"],
    # No "sycl" on Linux: its package is about a third of the Windows one because Intel's runtime (oneAPI) is NOT inside it, so it needs a separate install
    # MultiProxy cannot do for the player. Refused plainly rather than offered.
}
LINUX_ARM64 = {
    "cpu": [r"llama-{b}-bin-ubuntu-arm64\.tar\.gz"],
    "vulkan": [r"llama-{b}-bin-ubuntu-vulkan-arm64\.tar\.gz"],
}
SYSTEMS = {"win": WINDOWS, "linux": LINUX, "linux-arm64": LINUX_ARM64}


class BuildError(Exception):
    """A plain reason a build can't be used (never a stack trace)."""


def build_number(tag) -> int:
    """11433 for "b11433"; 0 for anything that is not a numbered build ("v0.6.0", None, "b12x")."""
    m = BUILD_TAG.match(str(tag or ""))
    return int(m.group(1)) if m else 0


def newest_numbered(releases) -> Optional[dict]:
    """The release with the highest `bNNNNN` number. The stub "latest" release, drafts and anything without a build number are never chosen."""
    best, best_n = None, 0
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        n = build_number(r.get("tag_name"))
        if n > best_n:
            best, best_n = r, n
    return best


def release_for(releases, build: str) -> Optional[dict]:
    """The release tagged exactly `build` ("b11433"), if the list has it."""
    n = build_number(build)
    return next((r for r in (releases if isinstance(releases, list) else []) if isinstance(r, dict) and n and build_number(r.get("tag_name")) == n), None)


def files_for(release: dict, kind: str, system: str) -> list[tuple[str, int, str]]:
    """[(file name, size in bytes, sha256)] for `kind` ("vulkan", "rocm", ...) in this release, on `system` ("win", "linux", "linux-arm64").
    Raises BuildError with a plain reason when the release does not hold it, or does not publish a checksum for a file."""
    patterns = SYSTEMS.get(system, {}).get(kind)
    if not patterns:
        raise BuildError("That version isn't available for this computer.")
    tag = str(release.get("tag_name") or "")
    if not build_number(tag):
        raise BuildError("That isn't a numbered build.")
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    out = []
    for pat in patterns:
        rx = re.compile("^" + pat.replace("{b}", re.escape(tag)) + "$")
        hit = [a for a in assets if rx.match(str(a.get("name") or ""))]
        if len(hit) != 1:
            raise BuildError(f"{tag} doesn't have the {kind} version for this computer.")
        a = hit[0]
        size = a.get("size")
        m = DIGEST.match(str(a.get("digest") or ""))
        if not isinstance(size, int) or size <= 0 or not m:
            raise BuildError(f"{tag} doesn't list a safety check for {a['name']}, so it wasn't used.")
        out.append((a["name"], size, m.group(1)))
    return out


async def fetch_releases(session, url: str = RELEASES_URL) -> list:
    """The newest releases, as GitHub lists them. [] when the list can't be read (offline, rate limited): an update check never complains."""
    try:
        async with session.get(url, headers={"Accept": "application/vnd.github+json"}) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except Exception:
        return []
    return data if isinstance(data, list) else []
