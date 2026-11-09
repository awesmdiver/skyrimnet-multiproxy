"""The picture the Routing page's Test sends to a Vision job, and the check that the AI really saw it (2026-11-08).

A reply alone proves nothing: an AI that was never given the picture still answers ("I don't see an image..."). So the Test sends a small known picture, a red square
with the word STAR on it, drawn here in code (no file), and counts the test as passed only when the answer names the colour or the word.
"""
import base64
import re
import struct
import zlib

QUESTION = "Describe what you see in this image in one short sentence."
LINE_NOT_SEEN = "The AI did not see the picture."
HINT_NOT_SEEN = "The AI answered without looking at the picture. Pick another AI for Vision, or check if this one supports it."

_GLYPHS = {                     # a 5 x 7 block letter each
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
}
WORD = "STAR"
_SIZE, _SCALE = 160, 5
_RED, _WHITE = (220, 20, 20), (255, 255, 255)


def _png(width: int, height: int, rows: list) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_picture_png() -> bytes:
    """A solid red square with the word STAR in large white letters, as PNG bytes."""
    pixels = [[_RED] * _SIZE for _ in range(_SIZE)]
    word_w = len(WORD) * 5 * _SCALE + (len(WORD) - 1) * _SCALE
    x0, y0 = (_SIZE - word_w) // 2, (_SIZE - 7 * _SCALE) // 2
    for i, ch in enumerate(WORD):
        gx = x0 + i * 6 * _SCALE
        for r, line in enumerate(_GLYPHS[ch]):
            for c, bit in enumerate(line):
                if bit == "1":
                    for dy in range(_SCALE):
                        for dx in range(_SCALE):
                            pixels[y0 + r * _SCALE + dy][gx + c * _SCALE + dx] = _WHITE
    return _png(_SIZE, _SIZE, [[v for px in row for v in px] for row in pixels])


def test_picture_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(test_picture_png()).decode("ascii")


def message_content() -> list:
    """The content of the test's one message: the question, then the picture (the OpenAI shape SkyrimNet's own Vision requests use)."""
    return [{"type": "text", "text": QUESTION}, {"type": "image_url", "image_url": {"url": test_picture_data_url()}}]


_SEEN = re.compile(r"\b(red|crimson|scarlet|star|stars)\b", re.I)


def saw_picture(answer: str) -> bool:
    """True when the answer names what is really in the picture (its colour or its word)."""
    return bool(_SEEN.search(answer or ""))
