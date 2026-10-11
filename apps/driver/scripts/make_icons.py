"""Draws public/icon-192.png and public/icon-512.png: the Simple TMS cube from
favicon.svg on the page colour, inside the middle 60 % so a maskable crop
keeps it whole. Standard library only. Run from apps/driver:
    python3 scripts/make_icons.py
"""
import struct
import zlib
from pathlib import Path

PAGE = (0xF3, 0xF6, 0xF4)
# the cube's three faces in favicon.svg's 32 x 32 box, with their colours
FACES = [
    ([(16, 3), (28, 10), (16, 17), (4, 10)], (0x5B, 0xC2, 0x8F)),
    ([(4, 10), (16, 17), (16, 30), (4, 23)], (0x1F, 0x8A, 0x5B)),
    ([(28, 10), (16, 17), (16, 30), (28, 23)], (0x14, 0x6B, 0x45)),
]
SAMPLES = 4  # per side, per pixel: smooth edges


def inside(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def draw(size: int) -> bytes:
    scale = size * 0.6 / 27          # the cube is 27 units tall (y 3 to 30)
    left = (size - 24 * scale) / 2 - 4 * scale
    top = (size - 27 * scale) / 2 - 3 * scale
    rows = []
    for py in range(size):
        row = bytearray([0])  # filter: none
        for px in range(size):
            totals = [0, 0, 0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x = ((px + (sx + 0.5) / SAMPLES) - left) / scale
                    y = ((py + (sy + 0.5) / SAMPLES) - top) / scale
                    colour = next((c for poly, c in FACES if inside(x, y, poly)), PAGE)
                    for i in range(3):
                        totals[i] += colour[i]
            row += bytes(t // (SAMPLES * SAMPLES) for t in totals)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw)
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    public = Path(__file__).resolve().parent.parent / "public"
    for size in (192, 512):
        (public / f"icon-{size}.png").write_bytes(draw(size))
        print(f"public/icon-{size}.png")
