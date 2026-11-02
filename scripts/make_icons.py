"""Draw newtube's app icons with nothing but the standard library (no image libraries needed).

    python3 scripts/make_icons.py        # rewrites frontend/public/icon-*.png, apple-touch-icon.png, icon.svg

The icon is a rounded play triangle in the accent colour on the app's dark background. It is drawn full-bleed with
the triangle inside the central 60%, so it is safe as a "maskable" icon (phones crop it to a circle or squircle).
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

BG, FG = (15, 15, 18), (110, 168, 254)      # --bg and --accent from app.css
OUT = Path(__file__).resolve().parent.parent / "frontend" / "public"
SS = 4                                       # supersampling per axis (smooth edges)


def inside(x: float, y: float) -> bool:
    """A play triangle, centred, about 46% of the icon wide (so well inside the maskable safe zone)."""
    ax, ay, bx, by, cx, cy = 0.36, 0.28, 0.36, 0.72, 0.72, 0.50
    d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
    d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
    d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def png(size: int) -> bytes:
    rows = bytearray()
    for py in range(size):
        rows.append(0)                                           # filter type: none
        for px in range(size):
            hits = sum(inside((px + (i + 0.5) / SS) / size, (py + (j + 0.5) / SS) / size)
                       for i in range(SS) for j in range(SS))
            a = hits / (SS * SS)
            rows += bytes(round(BG[k] + (FG[k] - BG[k]) * a) for k in range(3))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" fill="rgb{BG}"/>
  <polygon points="36,28 36,72 72,50" fill="rgb{FG}" stroke="rgb{FG}" stroke-width="4" stroke-linejoin="round"/>
</svg>
"""

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        (OUT / name).write_bytes(png(size))
        print("wrote", OUT / name)
    (OUT / "icon.svg").write_text(SVG)
    print("wrote", OUT / "icon.svg")
    sys.exit(0)
