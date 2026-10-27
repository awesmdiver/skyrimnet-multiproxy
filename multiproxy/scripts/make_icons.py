"""Makes MultiProxy's and the Courier's icons from the approved pictures (2026-10-02). Run by a maintainer; the output is committed.

    python scripts/make_icons.py

Reads   installer/icons/app-icon-source.jpg      (2048 x 2048, dark background, a glassy stone with six streams of light)
        design/icons/courier-envelope-swirl.jpg  (2048 x 2048, dark background, a stone-grey envelope with the six streams swirling over it)
Writes  installer/icons/multiproxy.ico           16, 20, 24, 32, 40, 48, 64, 128, 256 px
        installer/icons/courier.ico              the same, from the envelope picture (no badge: the envelope is the Courier's mark)
        installer/icons/app-icon.png             256 px, transparent: what the tray, the windows and the dashboard favicon draw from
        installer/icons/courier-icon.png         the same, for the Courier
        design/icons/courier-icon-sizes.png      a look-at sheet: both icons at 16, 24, 32, 48 and 256 px on dark and light

Each picture's background is a flat near-black field, so it is cut out onto transparency: everything connected to the picture's edge that is
close to the background colour goes, with a soft edge. The stone's (or the envelope's) black outline stops the cut, so the object stays whole.
The small sizes (16, 20, 24) are built from a tighter crop: for the stone, the swirl and the stone; for the envelope, the envelope's own
outline (the streams may be cropped at its edge), because the envelope is what tells the Courier apart from MultiProxy at tray size.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "installer" / "icons"
SOURCE = ICONS / "app-icon-source.jpg"
COURIER_SOURCE = ROOT / "design" / "icons" / "courier-envelope-swirl.jpg"
SHEET = ROOT / "design" / "icons" / "courier-icon-sizes.png"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SMALL = {16, 20, 24}
WORK = 1024


def cut_out(src: Image.Image) -> Image.Image:
    """The picture on transparency: the flat background removed with a soft edge."""
    img = src.convert("RGB").resize((WORK, WORK), Image.LANCZOS)
    bg = img.getpixel((8, 8))
    # distance from the background colour, per pixel
    diff = ImageChops.difference(img, Image.new("RGB", img.size, bg)).convert("L")
    near = diff.point(lambda v: 255 if v <= 14 else 0)
    # keep only the background that touches the picture's edge: flood it in from the corners
    flooded = near.copy()
    for xy in [(0, 0), (WORK - 1, 0), (0, WORK - 1), (WORK - 1, WORK - 1)]:
        ImageDraw.floodfill(flooded, xy, 128, thresh=0)
    background = flooded.point(lambda v: 255 if v == 128 else 0)
    alpha = ImageChops.invert(background).filter(ImageFilter.GaussianBlur(2.5))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    return out


def crop_square(img: Image.Image, margin: float, shrink: float = 1.0) -> Image.Image:
    """Crop to the picture's content (its transparent margin removed), made square with a little breathing room."""
    box = img.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    x0, y0, x1, y1 = box
    side = max(x1 - x0, y1 - y0) * (1 + margin) * shrink
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = cx - side / 2, cy - side / 2
    return img.crop((round(left), round(top), round(left + side), round(top + side)))


def sharpen(img: Image.Image, strong: bool) -> Image.Image:
    return img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=140 if strong else 70, threshold=1))


def frame(full: Image.Image, tight: Image.Image, size: int) -> Image.Image:
    base = tight if size in SMALL else full
    return sharpen(base.resize((size, size), Image.LANCZOS), size <= 32)


def write_ico(path: Path, frames: list) -> None:
    """An .ico of PNG frames, written by hand so every size is exactly the picture made for it (Pillow would shrink one image instead)."""
    import io
    blobs = []
    for im in frames:
        buf = io.BytesIO()
        im.save(buf, "PNG")
        blobs.append(buf.getvalue())
    head = b"\x00\x00\x01\x00" + len(frames).to_bytes(2, "little")
    offset = 6 + 16 * len(frames)
    entries = b""
    for im, blob in zip(frames, blobs):
        w = 0 if im.width >= 256 else im.width
        entries += bytes([w, w, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little") + len(blob).to_bytes(4, "little") + offset.to_bytes(4, "little")
        offset += len(blob)
    path.write_bytes(head + entries + b"".join(blobs))


# The envelope picture's outline box at WORK size (the approved picture is a fixed asset, so the box is written down, measured once from the 2048 px original:
# x 281 to 1765, y 485 to 1587). Outside it the picture has the envelope's soft grey glow; the colourful streams run past its edge.
ENV_BOX = (140, 242, 883, 794)
ENV_RADIUS = 24


def without_glow(img: Image.Image) -> Image.Image:
    """The same picture with the grey glow outside the envelope's outline removed. The streams stay (they are saturated colours; the glow is grey), so
    they still run past the envelope's edge. On a light background the glow reads as a grey halo; without it the outline stays crisp."""
    inside = Image.new("L", img.size, 0)
    x0, y0, x1, y1 = ENV_BOX
    ImageDraw.Draw(inside).rounded_rectangle((x0 - 4, y0 - 4, x1 + 4, y1 + 4), radius=ENV_RADIUS, fill=255)
    saturation = img.convert("RGB").convert("HSV").getchannel("S").point(lambda v: 255 if v > 90 else 0).filter(ImageFilter.MaxFilter(5))
    keep = ImageChops.lighter(inside, saturation).filter(ImageFilter.GaussianBlur(1.2))
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), keep))
    return out


def envelope_crops(cut_img: Image.Image) -> tuple:
    """(full, middle, tight) for the envelope picture. Full: everything, streams and glow (64 px and up). Middle: the whole picture without the glow
    (32 to 48 px). Tight: the envelope's own box without the glow, a little air around it, so its outline stays clear at 16 to 24 px (the streams are cropped
    at its edge)."""
    clean = without_glow(cut_img)
    full = crop_square(cut_img, 0.04)
    middle = crop_square(clean, 0.04)
    x0, y0, x1, y1 = ENV_BOX
    side = (x1 - x0) * 1.12
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tight = clean.crop((round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2)))
    return full.resize((WORK, WORK), Image.LANCZOS), middle.resize((WORK, WORK), Image.LANCZOS), tight.resize((WORK, WORK), Image.LANCZOS)


def sheet(mp: list, co: list) -> None:
    """Both icons at the sizes people meet them, on a dark and a light background, enlarged (nearest neighbour) so a 16 px icon can be judged by eye."""
    shown = [16, 24, 32, 48, 256]
    pad, label = 14, 18
    cell = 96
    width = pad + sum(max(cell, 0) + pad for _ in shown) + 0
    rows = [("MultiProxy", mp), ("Courier", co)]
    out = Image.new("RGB", (pad + len(shown) * (cell + pad), 2 * (len(rows) * (cell + label + pad)) + pad), (255, 255, 255))
    d = ImageDraw.Draw(out)
    y = 0
    for bg in ((24, 24, 28), (243, 243, 246)):
        h = len(rows) * (cell + label + pad) + pad
        d.rectangle((0, y, out.width, y + h), fill=bg)
        ink = (225, 225, 230) if bg[0] < 100 else (40, 40, 46)
        yy = y + pad
        for name, frames in rows:
            d.text((pad, yy), f"{name} on {'dark' if bg[0] < 100 else 'light'}", fill=ink)
            yy += label
            x = pad
            for s in shown:
                im = next(f for f in frames if f.width == s)
                scale = max(1, cell // s) if s < cell else 1
                shown_im = im.resize((s * scale, s * scale), Image.NEAREST) if s < cell else im.resize((cell, cell), Image.LANCZOS)
                out.paste(shown_im, (x + (cell - shown_im.width) // 2, yy + (cell - shown_im.height) // 2), shown_im)
                d.text((x, yy + cell - 10), f"{s}px", fill=ink)
                x += cell + pad
            yy += cell + pad
        y += h
    out = out.resize((out.width * 2, out.height * 2), Image.NEAREST)           # twice as big, so it can be judged by eye
    out.save(SHEET)


def main() -> None:
    for src in (SOURCE, COURIER_SOURCE):
        if not src.is_file():
            raise SystemExit(f"{src} is missing; the approved picture has to be there. Not drawing a substitute.")
    cut = cut_out(Image.open(SOURCE))
    full = crop_square(cut, 0.04)
    # the small sizes: the swirl and the stone's outline, the streams' far ends cropped away
    tight = crop_square(cut, 0.0, 0.8).resize((WORK, WORK), Image.LANCZOS)
    full = full.resize((WORK, WORK), Image.LANCZOS)
    mp_frames = [frame(full, tight, s) for s in SIZES]
    write_ico(ICONS / "multiproxy.ico", mp_frames)
    mp_frames[-1].save(ICONS / "app-icon.png")
    c_full, c_middle, c_tight = envelope_crops(cut_out(Image.open(COURIER_SOURCE)))
    co_frames = [frame(c_tight if s in SMALL else c_middle if s <= 48 else c_full, c_tight, s) for s in SIZES]
    write_ico(ICONS / "courier.ico", co_frames)
    co_frames[-1].save(ICONS / "courier-icon.png")
    sheet(mp_frames, co_frames)
    print("Wrote multiproxy.ico, courier.ico, app-icon.png, courier-icon.png in", ICONS, "and the sheet", SHEET)


if __name__ == "__main__":
    main()
