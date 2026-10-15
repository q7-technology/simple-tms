"""Pictures for the demo day, drawn here: a signature, a parcel left at a
door, a tail light with its globe out. No camera and no real people, and no
imaging library: a small PNG writer is enough for flat shapes."""
from __future__ import annotations

import math
import random
import struct
import zlib

Colour = tuple[int, int, int]


class Canvas:
    def __init__(self, width: int, height: int, background: Colour):
        self.width, self.height = width, height
        self.rows = [bytearray(bytes(background) * width) for _ in range(height)]

    def dot(self, x: int, y: int, colour: Colour) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.rows[y][3 * x:3 * x + 3] = bytes(colour)

    def rect(self, x0: int, y0: int, x1: int, y1: int, colour: Colour) -> None:
        x0, x1 = max(0, x0), min(self.width, x1)
        for y in range(max(0, y0), min(self.height, y1)):
            self.rows[y][3 * x0:3 * x1] = bytes(colour) * (x1 - x0)

    def disc(self, cx: int, cy: int, r: int, colour: Colour) -> None:
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.dot(x, y, colour)

    def grain(self, rnd: random.Random, amount: int) -> None:
        """A little noise, so a 'photo' doesn't look like a diagram."""
        for row in self.rows:
            for i in range(len(row)):
                row[i] = max(0, min(255, row[i] + rnd.randint(-amount, amount)))

    def png(self) -> bytes:
        raw = b"".join(b"\x00" + bytes(row) for row in self.rows)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


INK: Colour = (28, 40, 82)


def signature(seed: int) -> bytes:
    """A made-up signature: a looping pen line across a white pad."""
    rnd = random.Random(seed)
    c = Canvas(480, 160, (255, 255, 255))
    loops = rnd.randint(4, 7)
    lift = rnd.uniform(0.35, 0.6)
    steps = 1400
    for i in range(steps):
        t = i / steps
        x = 30 + 420 * t + 18 * math.sin(t * loops * 2 * math.pi + seed)
        y = (85 - 40 * math.sin(t * loops * math.pi) * math.cos(t * 3 + seed)
             + 14 * math.sin(t * 23) * lift)
        c.disc(int(x), int(y), 2, INK)
    # the underline flourish
    for x in range(60, 400):
        c.disc(x, int(132 + 4 * math.sin(x / 40 + seed)), 1, INK)
    return c.png()


def parcel_photo(seed: int) -> bytes:
    """A carton by a door, as a driver photographs a delivery."""
    rnd = random.Random(seed)
    c = Canvas(640, 480, rnd.choice([(214, 206, 190), (200, 210, 214), (226, 220, 206)]))
    c.rect(0, 360, 640, 480, (120, 116, 110))                    # the step
    door = rnd.choice([(52, 86, 72), (120, 54, 44), (44, 62, 96)])
    c.rect(200, 40, 440, 362, door)                              # the door
    c.rect(214, 54, 426, 348, tuple(min(255, v + 18) for v in door))
    c.disc(408, 210, 7, (196, 170, 90))                          # the handle
    x = rnd.randint(70, 150)
    w, h = rnd.randint(150, 210), rnd.randint(100, 150)
    c.rect(x, 372 - h, x + w, 372, (176, 132, 82))               # the carton
    c.rect(x, 372 - h, x + w, 372 - h + 10, (150, 110, 66))
    c.rect(x + w // 2 - 8, 372 - h, x + w // 2 + 8, 372, (196, 160, 112))   # tape
    c.rect(x + 14, 372 - h + 26, x + 74, 372 - h + 62, (248, 248, 244))     # label
    c.grain(rnd, 6)
    return c.png()


def tail_light_photo(seed: int) -> bytes:
    """A truck's tail light with the globe out, for a failed pre-start item."""
    rnd = random.Random(seed)
    c = Canvas(640, 480, (230, 232, 228))
    c.rect(0, 0, 640, 480, (236, 236, 232))                      # the tray side
    c.rect(180, 140, 460, 340, (40, 40, 44))                     # the housing
    c.rect(196, 156, 316, 324, (110, 30, 30))                    # the dead lamp
    c.rect(324, 156, 444, 324, (230, 150, 40))                   # the indicator
    for i in range(60):                                          # the crack
        c.disc(206 + i * 2, 170 + int(40 * math.sin(i / 7)), 1, (20, 20, 20))
    c.grain(rnd, 5)
    return c.png()
