#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sosfont - font tool for Story of Seasons: Trio of Towns (3DS).

The game font (romfs/Font/mainfont.bffnt) already contains Cyrillic, but in
a thin wide style that does not match its bold condensed Latin letters.
This tool redraws chosen characters from a TrueType font, in place: the
glyph slots, the character map and the file size stay the same, only the
pixels and the widths of the redrawn characters change.

Needs Pillow (pip install pillow).

    python sosfont.py info    rom/mainfont.bffnt
    python sosfont.py restyle rom/mainfont.bffnt PT_Sans-Narrow-Web-Bold.ttf out/mainfont.bffnt
    python sosfont.py preview out/mainfont.bffnt "Привет, ферма!" preview.png

Supported input: little-endian BFFNT with one A8 texture sheet, which is
what mainfont.bffnt and subfont.bffnt of this game are.
"""

import argparse
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

CYRILLIC = "Ё" + "".join(chr(c) for c in range(0x410, 0x450)) + "ё"


def _morton(k):
    x = y = 0
    for bit in range(3):
        x |= ((k >> (2 * bit)) & 1) << bit
        y |= ((k >> (2 * bit + 1)) & 1) << bit
    return x, y


MORTON = [_morton(k) for k in range(64)]


class FormatError(Exception):
    pass


class Bffnt:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        d = self.data
        if d[:4] != b"FFNT" or d[4:6] != b"\xff\xfe":
            raise FormatError("not a little-endian BFFNT font")
        pos = struct.unpack_from("<H", d, 6)[0]
        self.cmap = {}
        self.width_pos = []
        self.sheets = None
        while pos + 8 <= len(d):
            magic = bytes(d[pos:pos + 4])
            size = struct.unpack_from("<I", d, pos + 4)[0]
            if size < 8:
                break
            body = pos + 8
            if magic == b"FINF":
                self.linefeed = struct.unpack_from("<H", d, body + 4)[0]
            elif magic == b"TGLP":
                (self.cell_w, self.cell_h, self.sheets, _maxw, self.sheet_bytes,
                 self.baseline, self.fmt, self.cols, self.rows, self.sheet_w,
                 self.sheet_h, self.tex_pos) = struct.unpack_from("<BBBBIHHHHHHI", d, body)
            elif magic == b"CWDH":
                start, end = struct.unpack_from("<HH", d, body)
                for i in range(end - start + 1):
                    while len(self.width_pos) <= start + i:
                        self.width_pos.append(None)
                    self.width_pos[start + i] = body + 8 + 3 * i
            elif magic == b"CMAP":
                first, last, method = struct.unpack_from("<HHH", d, body)
                if method == 0:
                    base = struct.unpack_from("<H", d, body + 12)[0]
                    for c in range(first, last + 1):
                        self.cmap[c] = base + c - first
                elif method == 1:
                    for i in range(last - first + 1):
                        idx = struct.unpack_from("<H", d, body + 12 + 2 * i)[0]
                        if idx != 0xFFFF:
                            self.cmap[first + i] = idx
                else:
                    n = struct.unpack_from("<H", d, body + 12)[0]
                    for i in range(n):
                        c, idx = struct.unpack_from("<HH", d, body + 14 + 4 * i)
                        self.cmap[c] = idx
            pos += size
        if self.sheets != 1 or self.fmt != 8:
            raise FormatError("only fonts with one A8 texture sheet are supported "
                              "(sheets=%s, format=%s)" % (self.sheets, self.fmt))
        if self.sheet_bytes != self.sheet_w * self.sheet_h:
            raise FormatError("unexpected texture size")
        self.image = Image.new("L", (self.sheet_w, self.sheet_h))
        px = self.image.load()
        i = self.tex_pos
        for ty in range(0, self.sheet_h, 8):
            for tx in range(0, self.sheet_w, 8):
                for x, y in MORTON:
                    px[tx + x, ty + y] = d[i]
                    i += 1

    def has(self, ch):
        return ord(ch) in self.cmap

    def widths(self, ch):
        """(left bearing, glyph width, advance) of a character."""
        return struct.unpack_from("<bBB", self.data, self.width_pos[self.cmap[ord(ch)]])

    def set_widths(self, ch, left, glyph, advance):
        struct.pack_into("<bBB", self.data, self.width_pos[self.cmap[ord(ch)]],
                         left, glyph, advance)

    def cell(self, ch):
        idx = self.cmap[ord(ch)]
        return (idx % self.cols) * (self.cell_w + 1) + 1, (idx // self.cols) * (self.cell_h + 1) + 1

    def glyph(self, ch):
        x, y = self.cell(ch)
        return self.image.crop((x, y, x + self.cell_w, y + self.cell_h))

    def put_glyph(self, ch, img):
        x, y = self.cell(ch)
        self.image.paste(0, (x, y, x + self.cell_w, y + self.cell_h))
        self.image.paste(img, (x, y))

    def save(self, path):
        px = self.image.load()
        i = self.tex_pos
        for ty in range(0, self.sheet_h, 8):
            for tx in range(0, self.sheet_w, 8):
                for x, y in MORTON:
                    self.data[i] = px[tx + x, ty + y]
                    i += 1
        with open(path, "wb") as f:
            f.write(self.data)

    def render(self, text, scale=3):
        """Draw text the way the game lays it out (black on white)."""
        lines = text.split("\n")
        width = 4 + max(sum(self.widths(c)[2] for c in line if self.has(c)) for line in lines)
        out = Image.new("L", (width, len(lines) * self.linefeed + 2), 0)
        for row, line in enumerate(lines):
            x = 2
            for c in line:
                if not self.has(c):
                    continue
                left, gw, advance = self.widths(c)
                if gw:
                    mask = self.glyph(c).crop((0, 0, gw, self.cell_h))
                    out.paste(255, (x + left, 1 + row * self.linefeed), mask)
                x += advance
        out = Image.eval(out, lambda v: 255 - v)
        return out.resize((out.width * scale, out.height * scale), Image.NEAREST)


def ink_box(font, ch):
    """Bounding box of the visible pixels of a glyph inside its cell."""
    return font.glyph(ch).point(lambda v: 255 if v > 40 else 0).getbbox()


def load_ttf(path, size, weight):
    ttf = ImageFont.truetype(path, size)
    if weight:
        ttf.set_variation_by_axes([weight])
    return ttf


def fit_size(path, weight, cap_height):
    """Find the font size at which the capital letter 'Н' is cap_height px tall."""
    for tenth in range(80, 260):
        ttf = load_ttf(path, tenth / 10, weight)
        box = ttf.getbbox("Н")
        if box[3] - box[1] >= cap_height:
            return tenth / 10
    raise FormatError("cannot fit this font to the cell")


def cmd_info(args):
    font = Bffnt(args.font)
    print("cell %dx%d, sheet %dx%d, %d glyphs, line height %d"
          % (font.cell_w, font.cell_h, font.sheet_w, font.sheet_h, len(font.cmap), font.linefeed))
    for name, sample in (("Latin capital H", "H"), ("Latin small x", "x"),
                         ("Cyrillic capital Н", "Н"), ("Cyrillic small х", "х")):
        if font.has(sample):
            box = ink_box(font, sample)
            print("%-20s rows %d-%d, widths %s" % (name, box[1], box[3] - 1, font.widths(sample)))
    missing = [c for c in CYRILLIC if not font.has(c)]
    print("Cyrillic letters missing: %s" % ("".join(missing) or "none"))
    return 0


def cmd_restyle(args):
    font = Bffnt(args.font)
    chars = args.chars or CYRILLIC
    absent = [c for c in chars if not font.has(c)]
    if absent:
        raise FormatError("the font has no slot for: %s" % "".join(absent))

    # match the Latin capitals of the game font: same top row and baseline
    box = ink_box(font, "H")
    cap_top, baseline = box[1], box[3]
    size = args.size or fit_size(args.ttf, args.weight, baseline - cap_top)
    ttf = load_ttf(args.ttf, size, args.weight)

    pad = 8
    clipped = []
    for ch in chars:
        canvas = Image.new("L", (font.cell_w + 2 * pad + 16, font.cell_h), 0)
        ImageDraw.Draw(canvas).text((pad, baseline + args.y_offset), ch, font=ttf,
                                    fill=255, anchor="ls")
        if args.gain != 1.0:
            canvas = canvas.point(lambda v: min(255, int(v * args.gain)))
        ink = canvas.point(lambda v: 255 if v > 24 else 0).getbbox()
        if ink is None:
            raise FormatError("the TrueType font has no glyph for %s" % ch)
        left, right = ink[0], ink[2]
        if right - left > font.cell_w:
            clipped.append(ch)
            right = left + font.cell_w
        glyph = canvas.crop((left, 0, right, font.cell_h))
        font.put_glyph(ch, glyph)
        bearing = max(0, left - pad)
        advance = max(right - left + bearing, int(round(ttf.getlength(ch))) + args.spacing)
        font.set_widths(ch, bearing, right - left, advance)

    font.save(args.out)
    print("redrew %d characters at size %.1f, wrote %s" % (len(chars), size, args.out))
    if clipped:
        print("too wide for the cell and clipped: %s" % "".join(clipped))
    return 0


def cmd_preview(args):
    font = Bffnt(args.font)
    text = args.text.replace("\\n", "\n")
    font.render(text, args.scale).save(args.out)
    print("wrote %s" % args.out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Font tool for Story of Seasons: Trio of Towns (3DS)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="show font metrics")
    p.add_argument("font")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("restyle", help="redraw characters from a TrueType font")
    p.add_argument("font", help="original .bffnt")
    p.add_argument("ttf", help="TrueType font to take the shapes from")
    p.add_argument("out", help="output .bffnt")
    p.add_argument("--chars", help="characters to redraw (default: the Russian alphabet)")
    p.add_argument("--size", type=float, help="font size in pixels (default: match Latin capitals)")
    p.add_argument("--weight", type=int, help="weight for variable fonts, e.g. 700")
    p.add_argument("--y-offset", type=int, default=0, help="move glyphs down by this many pixels")
    p.add_argument("--spacing", type=int, default=0, help="extra pixels after every character")
    p.add_argument("--gain", type=float, default=1.0, help="darken glyphs, e.g. 1.3")
    p.set_defaults(func=cmd_restyle)

    p = sub.add_parser("preview", help="render a text sample to a PNG")
    p.add_argument("font")
    p.add_argument("text", help=r"text, use \n for a new line")
    p.add_argument("out")
    p.add_argument("--scale", type=int, default=3)
    p.set_defaults(func=cmd_preview)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (FormatError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
