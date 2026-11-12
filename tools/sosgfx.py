#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sosgfx - interface image tool for Story of Seasons: Trio of Towns (3DS).

Button prompts, window titles, season names and similar labels are not
text: they are small images inside the SARC archives of romfs/Layout.
This tool extracts those images to PNG and puts edited PNGs back.

Needs Pillow (pip install pillow).

    python sosgfx.py list    rom/Layout/ListGuideButton.arc
    python sosgfx.py extract rom/Layout/ListGuideButton.arc gfx/ListGuideButton
    python sosgfx.py pack    rom/Layout/ListGuideButton.arc gfx/ListGuideButton out/Layout/ListGuideButton.arc

`pack` only touches images whose PNG differs from the original, and writes
them in place: an edited PNG must keep the size of the original image, and
the archive keeps its size and layout. Images stored in the compressed
ETC1 formats are listed but can be neither extracted nor replaced.
"""

import argparse
import os
import struct
import sys

from PIL import Image

# BFLIM pixel formats that are stored uncompressed: id -> (name, bytes per pixel)
FORMATS = {
    0: ("L8", 1), 1: ("A8", 1), 2: ("LA4", 1), 3: ("LA8", 2), 5: ("RGB565", 2),
    6: ("RGB8", 3), 7: ("RGBA5551", 2), 8: ("RGBA4", 2), 9: ("RGBA8", 4),
}
OTHER_FORMATS = {4: "HILO8", 10: "ETC1", 11: "ETC1A4", 12: "L4", 13: "A4"}


def _morton(k):
    x = y = 0
    for bit in range(3):
        x |= ((k >> (2 * bit)) & 1) << bit
        y |= ((k >> (2 * bit + 1)) & 1) << bit
    return x, y


MORTON = [_morton(k) for k in range(64)]


class FormatError(Exception):
    pass


def _pow2(n):
    v = 8
    while v < n:
        v *= 2
    return v


# --------------------------------------------------------------------------
# SARC
# --------------------------------------------------------------------------

def read_sarc(data):
    """Return [(name, start, end)] with absolute offsets of every file."""
    if data[:4] != b"SARC" or data[6:8] != b"\xff\xfe":
        raise FormatError("not a little-endian SARC archive")
    data_off = struct.unpack_from("<I", data, 0x0C)[0]
    if data[0x14:0x18] != b"SFAT":
        raise FormatError("SFAT block not found")
    count = struct.unpack_from("<H", data, 0x1A)[0]
    names_base = 0x20 + 16 * count + 8
    out = []
    for i in range(count):
        _hash, attr, start, end = struct.unpack_from("<IIII", data, 0x20 + 16 * i)
        pos = names_base + (attr & 0xFFFF) * 4
        name = data[pos:data.index(b"\0", pos)].decode("ascii")
        out.append((name, data_off + start, data_off + end))
    return out


# --------------------------------------------------------------------------
# BFLIM
# --------------------------------------------------------------------------

class Flim:
    def __init__(self, blob):
        pos = blob.rfind(b"FLIM")
        if pos < 0:
            raise FormatError("not a BFLIM image")
        header = struct.unpack_from("<H", blob, pos + 6)[0]
        if blob[pos + header:pos + header + 4] != b"imag":
            raise FormatError("imag block not found")
        (self.width, self.height, _align, self.fmt,
         self.swizzle) = struct.unpack_from("<HHHBB", blob, pos + header + 8)
        self.pixels_end = pos
        self.rotated = self.swizzle in (4, 8)
        # size of the image as it is stored (rotated images are stored sideways)
        self.stored = (self.height, self.width) if self.rotated else (self.width, self.height)

    @property
    def format_name(self):
        if self.fmt in FORMATS:
            return FORMATS[self.fmt][0]
        return OTHER_FORMATS.get(self.fmt, "format %d" % self.fmt)

    @property
    def editable(self):
        return self.fmt in FORMATS and self.swizzle in (0, 4)


def _unpack(fmt, b, i):
    if fmt == 8:
        v = b[i] | (b[i + 1] << 8)
        return ((v >> 12) & 15) * 17, ((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17
    if fmt == 9:
        return b[i + 3], b[i + 2], b[i + 1], b[i]
    if fmt == 5:
        v = b[i] | (b[i + 1] << 8)
        return (v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31, 255
    if fmt == 7:
        v = b[i] | (b[i + 1] << 8)
        return (((v >> 11) & 31) * 255 // 31, ((v >> 6) & 31) * 255 // 31,
                ((v >> 1) & 31) * 255 // 31, 255 * (v & 1))
    if fmt == 6:
        return b[i + 2], b[i + 1], b[i], 255
    if fmt == 3:
        return b[i + 1], b[i + 1], b[i + 1], b[i]
    if fmt == 2:
        lum = (b[i] >> 4) * 17
        return lum, lum, lum, (b[i] & 15) * 17
    if fmt == 0:
        return b[i], b[i], b[i], 255
    return 255, 255, 255, b[i]                      # A8


def _pack(fmt, c):
    r, g, b, a = c
    if fmt == 8:
        v = ((r + 8) // 17 << 12) | ((g + 8) // 17 << 8) | ((b + 8) // 17 << 4) | ((a + 8) // 17)
        return bytes((v & 255, v >> 8))
    if fmt == 9:
        return bytes((a, b, g, r))
    if fmt == 5:
        v = ((r * 31 + 127) // 255 << 11) | ((g * 63 + 127) // 255 << 5) | ((b * 31 + 127) // 255)
        return bytes((v & 255, v >> 8))
    if fmt == 7:
        v = (((r * 31 + 127) // 255 << 11) | ((g * 31 + 127) // 255 << 6)
             | ((b * 31 + 127) // 255 << 1) | (1 if a >= 128 else 0))
        return bytes((v & 255, v >> 8))
    if fmt == 6:
        return bytes((b, g, r))
    lum = (r * 299 + g * 587 + b * 114 + 500) // 1000
    if fmt == 3:
        return bytes((a, lum))
    if fmt == 2:
        return bytes((((lum + 8) // 17 << 4) | ((a + 8) // 17),))
    if fmt == 0:
        return bytes((lum,))
    return bytes((a,))                              # A8


def decode(blob):
    """Decode a BFLIM image to an upright RGBA picture."""
    info = Flim(blob)
    if not info.editable:
        return None
    bpp = FORMATS[info.fmt][1]
    sw, sh = _pow2(info.stored[0]), _pow2(info.stored[1])
    img = Image.new("RGBA", (sw, sh))
    px = img.load()
    i = 0
    for ty in range(0, sh, 8):
        for tx in range(0, sw, 8):
            for x, y in MORTON:
                if i + bpp > info.pixels_end:
                    break
                px[tx + x, ty + y] = _unpack(info.fmt, blob, i)
                i += bpp
    img = img.crop((0, 0, info.stored[0], info.stored[1]))
    if info.rotated:
        img = img.transpose(Image.TRANSPOSE).transpose(Image.FLIP_TOP_BOTTOM)
    return img


def encode(blob, img):
    """Return a copy of a BFLIM file with its pixels replaced by `img`."""
    info = Flim(blob)
    if not info.editable:
        raise FormatError("%s images cannot be replaced" % info.format_name)
    if img.size != (info.width, info.height):
        raise FormatError("image must be %dx%d, got %dx%d"
                          % (info.width, info.height, img.width, img.height))
    img = img.convert("RGBA")
    if info.rotated:
        img = img.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.TRANSPOSE)
    bpp = FORMATS[info.fmt][1]
    sw, sh = _pow2(info.stored[0]), _pow2(info.stored[1])
    px = img.load()
    out = bytearray(blob)
    i = 0
    for ty in range(0, sh, 8):
        for tx in range(0, sw, 8):
            for x, y in MORTON:
                if i + bpp > info.pixels_end:
                    break
                # pixels outside the picture are padding and keep their bytes
                if tx + x < info.stored[0] and ty + y < info.stored[1]:
                    out[i:i + bpp] = _pack(info.fmt, px[tx + x, ty + y])
                i += bpp
    return bytes(out)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def png_path(outdir, name):
    return os.path.join(outdir, *name.split("/"))[:-len(".bflim")] + ".png"


def cmd_list(args):
    with open(args.arc, "rb") as f:
        data = f.read()
    for name, start, end in read_sarc(data):
        if name.endswith(".bflim"):
            info = Flim(data[start:end])
            note = "" if info.editable else "  (cannot be edited)"
            print("%-44s %4dx%-4d %s%s" % (name, info.width, info.height, info.format_name, note))
        else:
            print("%-44s %d bytes" % (name, end - start))
    return 0


def cmd_extract(args):
    with open(args.arc, "rb") as f:
        data = f.read()
    done = skipped = 0
    for name, start, end in read_sarc(data):
        if not name.endswith(".bflim"):
            continue
        img = decode(data[start:end])
        if img is None:
            skipped += 1
            continue
        out = png_path(args.outdir, name)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out)
        done += 1
    print("extracted %d images to %s" % (done, args.outdir))
    if skipped:
        print("%d images are in compressed formats and were skipped" % skipped)
    return 0


def cmd_pack(args):
    with open(args.arc, "rb") as f:
        data = bytearray(f.read())
    changed = 0
    for name, start, end in read_sarc(data):
        if not name.endswith(".bflim"):
            continue
        src = png_path(args.pngdir, name)
        if not os.path.exists(src):
            continue
        blob = bytes(data[start:end])
        original = decode(blob)
        if original is None:
            continue
        new = Image.open(src).convert("RGBA")
        if new.size == original.size and new.tobytes() == original.tobytes():
            continue
        try:
            data[start:end] = encode(blob, new)
        except FormatError as exc:
            raise FormatError("%s: %s" % (src, exc))
        changed += 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(data)
    print("replaced %d images, wrote %s" % (changed, args.out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Interface image tool for Story of Seasons: Trio of Towns (3DS)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list the files inside a Layout .arc")
    p.add_argument("arc")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="extract the images of an .arc to PNG")
    p.add_argument("arc")
    p.add_argument("outdir")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("pack", help="put edited PNG images back into an .arc")
    p.add_argument("arc", help="original .arc from the game")
    p.add_argument("pngdir", help="directory written by extract, with edited PNGs")
    p.add_argument("out", help="output .arc")
    p.set_defaults(func=cmd_pack)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (FormatError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
