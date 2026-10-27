"""The dashboard scripts as a file for a Node harness to read. They are too long for one environment variable on Linux (128 KB per variable)."""
import json
import tempfile


def _ui_js_file(srcs: list) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(srcs, f)
    return f.name
