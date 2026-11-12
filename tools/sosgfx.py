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
the archive keeps its size and layout.

Many images use the compressed ETC1 / ETC1A4 formats. They are extracted
like the others. When such an image is edited, only the 4x4 pixel blocks
that actually changed are compressed again, so untouched parts keep their
exact original bytes. ETC1 is lossy: redrawn areas come out slightly
softer than the PNG, just like the game's own images.
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
ETC_FORMATS = {10: "ETC1", 11: "ETC1A4"}
OTHER_FORMATS = {4: "HILO8", 12: "L4", 13: "A4"}
ETC_MODIFIERS = ((2, 8), (5, 17), (9, 29), (13, 42), (18, 60), (24, 80), (33, 106), (47, 183))
ETC_BLOCKS = ((0, 0), (4, 0), (0, 4), (4, 4))      # order of 4x4 blocks in an 8x8 tile


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
        self.etc = self.fmt in ETC_FORMATS
        # size of the image as it is stored (rotated images are stored sideways)
        self.stored = (self.height, self.width) if self.rotated else (self.width, self.height)

    @property
    def format_name(self):
        if self.fmt in FORMATS:
            return FORMATS[self.fmt][0]
        if self.etc:
            return ETC_FORMATS[self.fmt]
        return OTHER_FORMATS.get(self.fmt, "format %d" % self.fmt)

    @property
    def editable(self):
        return (self.fmt in FORMATS or self.etc) and self.swizzle in (0, 4, 8)

    def upright(self, img):
        """Turn an image from its stored orientation to the way it is shown."""
        if self.swizzle == 4:
            return img.transpose(Image.TRANSPOSE).transpose(Image.FLIP_TOP_BOTTOM)
        if self.swizzle == 8:
            return img.transpose(Image.TRANSPOSE)
        return img

    def as_stored(self, img):
        if self.swizzle == 4:
            return img.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.TRANSPOSE)
        if self.swizzle == 8:
            return img.transpose(Image.TRANSPOSE)
        return img


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


# ---- ETC1 ----------------------------------------------------------------

def _clamp(v):
    return 0 if v < 0 else 255 if v > 255 else v


def _etc_block_colours(block):
    """Decode one 64-bit ETC1 block to {(x, y): (r, g, b)}."""
    hi, lo = block >> 32, block & 0xFFFFFFFF
    flip = hi & 1
    if hi & 2:                                    # differential mode, 5+3 bits
        def signed(v):
            return v - 8 if v & 4 else v
        r, g, b = (hi >> 27) & 31, (hi >> 19) & 31, (hi >> 11) & 31
        r2 = (r + signed((hi >> 24) & 7)) & 31
        g2 = (g + signed((hi >> 16) & 7)) & 31
        b2 = (b + signed((hi >> 8) & 7)) & 31
        bases = (tuple((v << 3) | (v >> 2) for v in (r, g, b)),
                 tuple((v << 3) | (v >> 2) for v in (r2, g2, b2)))
    else:                                         # individual mode, 4+4 bits
        bases = (tuple(((hi >> n) & 15) * 17 for n in (28, 20, 12)),
                 tuple(((hi >> n) & 15) * 17 for n in (24, 16, 8)))
    tables = (ETC_MODIFIERS[(hi >> 5) & 7], ETC_MODIFIERS[(hi >> 2) & 7])
    out = {}
    for x in range(4):
        for y in range(4):
            i = x * 4 + y
            index = (((lo >> (i + 16)) & 1) << 1) | ((lo >> i) & 1)
            sub = (y >= 2) if flip else (x >= 2)
            mod = tables[sub][index & 1]
            if index & 2:
                mod = -mod
            base = bases[sub]
            out[(x, y)] = (_clamp(base[0] + mod), _clamp(base[1] + mod), _clamp(base[2] + mod))
    return out


def _etc_fit(pixels, base):
    """Best table and pixel indices for one half block with a given base colour."""
    best = None
    for table, (small, large) in enumerate(ETC_MODIFIERS):
        shades = [tuple(_clamp(c + m) for c in base) for m in (small, large, -small, -large)]
        error = 0
        indices = []
        for colour, weight in pixels:
            errs = [sum((a - b) * (a - b) for a, b in zip(colour, shade)) for shade in shades]
            index = min(range(4), key=errs.__getitem__)
            indices.append(index)
            error += errs[index] * weight
            if best is not None and error >= best[0]:
                break
        else:
            if best is None or error < best[0]:
                best = (error, table, indices)
    return best


def _etc_encode_block(colours, weights):
    """Compress 16 pixels ({(x, y): (r, g, b)}) into a 64-bit ETC1 block."""
    best = None
    for flip in (0, 1):
        halves = ([], [])
        for x in range(4):
            for y in range(4):
                halves[(y >= 2) if flip else (x >= 2)].append((x, y))
        data = [[(colours[p], weights[p]) for p in half] for half in halves]
        means = []
        for half in data:
            total = sum(w for _c, w in half) or 1
            use = half if any(w for _c, w in half) else [(c, 1) for c, _w in half]
            total = sum(w for _c, w in use)
            means.append(tuple(sum(c[k] * w for c, w in use) / total for k in range(3)))

        # individual mode: two independent 4-bit base colours
        fits = []
        for half, mean in zip(data, means):
            cands = []
            for shift in (0, -1, 1):
                q = tuple(min(15, max(0, int(round(m / 17)) + shift)) for m in mean)
                fit = _etc_fit(half, tuple(v * 17 for v in q))
                if fit:
                    cands.append((fit[0], q, fit[1], fit[2]))
            fits.append(min(cands))
        error = fits[0][0] + fits[1][0]
        if best is None or error < best[0]:
            q1, q2 = fits[0][1], fits[1][1]
            hi = ((q1[0] << 28) | (q2[0] << 24) | (q1[1] << 20) | (q2[1] << 16)
                  | (q1[2] << 12) | (q2[2] << 8) | (fits[0][2] << 5) | (fits[1][2] << 2) | flip)
            best = (error, hi, halves, fits[0][3], fits[1][3])

        # differential mode: 5-bit base colour and a 3-bit step to the second one
        q1 = tuple(min(31, max(0, int(round(m * 31 / 255)))) for m in means[0])
        q2 = tuple(min(31, max(0, int(round(m * 31 / 255)))) for m in means[1])
        q2 = tuple(min(a + 3, max(a - 4, b)) for a, b in zip(q1, q2))
        fit1 = _etc_fit(data[0], tuple((v << 3) | (v >> 2) for v in q1))
        fit2 = _etc_fit(data[1], tuple((v << 3) | (v >> 2) for v in q2))
        error = fit1[0] + fit2[0]
        if error < best[0]:
            d = tuple((b - a) & 7 for a, b in zip(q1, q2))
            hi = ((q1[0] << 27) | (d[0] << 24) | (q1[1] << 19) | (d[1] << 16)
                  | (q1[2] << 11) | (d[2] << 8) | (fit1[1] << 5) | (fit2[1] << 2) | 2 | flip)
            best = (error, hi, halves, fit1[2], fit2[2])

    _error, hi, halves, idx1, idx2 = best
    lo = 0
    for half, indices in ((halves[0], idx1), (halves[1], idx2)):
        for (x, y), index in zip(half, indices):
            i = x * 4 + y
            lo |= ((index >> 1) & 1) << (i + 16) | (index & 1) << i
    return (hi << 32) | lo


def _etc_decode(blob, info, size):
    alpha = info.fmt == 11
    img = Image.new("RGBA", size)
    px = img.load()
    i = 0
    for ty in range(0, size[1], 8):
        for tx in range(0, size[0], 8):
            for bx, by in ETC_BLOCKS:
                a = 0xFFFFFFFFFFFFFFFF
                if alpha:
                    a = int.from_bytes(blob[i:i + 8], "little")
                    i += 8
                block = _etc_block_colours(int.from_bytes(blob[i:i + 8], "little"))
                i += 8
                for (x, y), colour in block.items():
                    px[tx + bx + x, ty + by + y] = colour + (((a >> (4 * (x * 4 + y))) & 15) * 17,)
    return img


def _etc_encode(blob, info, new, size):
    """Recompress only the 4x4 blocks whose pixels differ from the original."""
    alpha = info.fmt == 11
    old = _etc_decode(blob, info, size).load()
    px = new.load()
    out = bytearray(blob)
    i = 0
    for ty in range(0, size[1], 8):
        for tx in range(0, size[0], 8):
            for bx, by in ETC_BLOCKS:
                cells = [(x, y) for x in range(4) for y in range(4)]
                inside = tx + bx < info.stored[0] and ty + by < info.stored[1]
                changed = inside and any(
                    px[tx + bx + x, ty + by + y] != old[tx + bx + x, ty + by + y]
                    for x, y in cells)
                if changed:
                    pixels = {c: px[tx + bx + c[0], ty + by + c[1]] for c in cells}
                    if alpha:
                        a = 0
                        for x, y in cells:
                            a |= ((pixels[(x, y)][3] + 8) // 17) << (4 * (x * 4 + y))
                        out[i:i + 8] = a.to_bytes(8, "little")
                    weights = {c: (1 if not alpha or pixels[c][3] >= 9 else 0) for c in cells}
                    block = _etc_encode_block({c: pixels[c][:3] for c in cells}, weights)
                    out[i + (8 if alpha else 0):i + (16 if alpha else 8)] = block.to_bytes(8, "little")
                i += 16 if alpha else 8
    return bytes(out)


# ---- whole images --------------------------------------------------------

def decode(blob):
    """Decode a BFLIM image to an upright RGBA picture."""
    info = Flim(blob)
    if not info.editable:
        return None
    sw, sh = _pow2(info.stored[0]), _pow2(info.stored[1])
    if info.etc:
        img = _etc_decode(blob, info, (sw, sh))
    else:
        bpp = FORMATS[info.fmt][1]
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
    return info.upright(img.crop((0, 0, info.stored[0], info.stored[1])))


def encode(blob, img):
    """Return a copy of a BFLIM file with its pixels replaced by `img`."""
    info = Flim(blob)
    if not info.editable:
        raise FormatError("%s images cannot be replaced" % info.format_name)
    if img.size != (info.width, info.height):
        raise FormatError("image must be %dx%d, got %dx%d"
                          % (info.width, info.height, img.width, img.height))
    img = info.as_stored(img.convert("RGBA"))
    sw, sh = _pow2(info.stored[0]), _pow2(info.stored[1])
    if info.etc:
        # padding outside the picture keeps the pixels it already has
        padded = _etc_decode(blob, info, (sw, sh))
        padded.paste(img, (0, 0))
        return _etc_encode(blob, info, padded, (sw, sh))
    bpp = FORMATS[info.fmt][1]
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
        print("%d images are in formats this tool cannot read and were skipped" % skipped)
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
