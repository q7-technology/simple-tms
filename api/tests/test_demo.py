"""The demo day: made through the API, from Simple WMS freight, and only
into an empty database."""
import struct
import zlib

from tms import demo_pictures


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    zlib.decompress(data[data.index(b"IDAT") + 4:])  # the pixels read back
    return width, height


# --- the pictures ----------------------------------------------------------------

def test_a_signature_is_a_small_png_and_each_is_its_own():
    one, two = demo_pictures.signature(1), demo_pictures.signature(2)
    assert png_size(one) == (480, 160)
    assert len(one) < 1_000_000
    assert one != two
    assert demo_pictures.signature(1) == one


def test_photos_are_pngs_under_the_photo_limit():
    for picture in (demo_pictures.parcel_photo(3), demo_pictures.tail_light_photo(3)):
        assert png_size(picture) == (640, 480)
        assert len(picture) < 5_000_000
    assert demo_pictures.parcel_photo(3) != demo_pictures.parcel_photo(4)
