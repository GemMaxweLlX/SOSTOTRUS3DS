#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soslabels - redraw text labels inside interface images.

Takes PNG images extracted by sosgfx.py, erases the English word in each
one and draws a Russian word in its place, keeping the button icon and
copying the fill and outline colours of the original lettering.

    python soslabels.py translation/gfx/labels.json ListGuideButton.arc \\
           gfx/ListGuideButton RussoOne-Regular.ttf

The labels file maps an archive name to {image name: text}. An image name
without a "_0"/"_1" suffix applies to every state of that button. The key
"_icon": true marks archives whose images start with a round button icon.
"_fill" and "_ring" set the letter colour and an inner outline ("#rrggbb",
"edge" for the outline colour of the original word, or "auto" for the
colour inside the original letters), "_size" the font
size in pixels and "_stroke" the outline width. With "box": [x0, y0, x1, y1] a word inside a
bigger picture is replaced: the box is painted with its corner colour and
the new word is drawn there in "fill" colour, without an outline
("align": "center" centres it in the box; "rowfill": true repaints
each row with its own colour from the left edge of the box, for buttons
with a gradient; "rowfill": "mode" uses the most common colour of each
row, for bands the old word partly covers; "outside" the most common
colour of the same row outside the box; "inner" everything between the
grey rims of a bubble; a number takes the colours
from that column). "outline": "#rrggbb" and "width" give the letters an
outline; "parts": [{"text": ..., "box": [...]}, ...] replaces more words
in the same picture with the same style. "dx"/"dy" shift a word by pixels; "cut": x sets where a button icon ends. A label may also be an object such as
{"text": "Весна", "fill": "#ff4f8f"} to override the style for one image.
"align": "left" starts a word where the old one started, "auto" does
that only when the old word sat at the left. "_fit"/"fit": true keeps a word no wider than the original one and
reports words that had to be shrunk or squeezed. "keep_box": true keeps the new word inside the area of the original one,
for pictures of which the game shows only that part.
Needs Pillow.
"""

import collections
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

OPAQUE = 96


def text_region(img, icon, cut=None):
    """Columns that hold the lettering. With `icon`, the round button icon
    at the left edge is left alone and the lettering starts after it."""
    px = img.load()
    count = [sum(1 for y in range(img.height) if px[x, y][3] > OPAQUE)
             for x in range(img.width)]
    xs = [x for x, n in enumerate(count) if n]
    if not xs:
        return None
    first, last = xs[0], xs[-1]
    if not icon:
        return first, last + 1
    if cut is not None:                   # where the icon ends, if given
        return cut, last + 1
    # the icon and the first letter touch: split at the thinnest column
    lo, hi = first + 12, min(first + 24, last)
    split = min(range(lo, hi), key=lambda x: (count[x], x))
    return split, last + 1


def edge_colour(img, x0, x1):
    """Colour of the outermost outline of the lettering between two columns."""
    px = img.load()
    w, h = img.size
    seen = collections.Counter()
    for y in range(h):
        for x in range(x0, x1):
            if px[x, y][3] < 250:
                continue
            for i, j in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if not (0 <= i < w and 0 <= j < h) or px[i, j][3] < OPAQUE:
                    seen[px[x, y]] += 1
                    break
    return seen.most_common(1)[0][0] if seen else (0, 0, 0, 255)


def fill_colour(img, x0, x1, edge):
    """Most common colour inside the letters (pixels away from the outline)."""
    px = img.load()
    w, h = img.size
    seen = collections.Counter()
    for y in range(1, h - 1):
        for x in range(max(x0, 1), min(x1, w - 1)):
            c = px[x, y]
            if c[3] < 250 or c == edge:
                continue
            if all(px[i, j][3] >= 250 for i, j in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))):
                seen[c] += 1
    return seen.most_common(1)[0][0] if seen else (255, 255, 255, 255)


def parse_colour(value, edge, auto=None):
    if value in (None, ""):
        return None
    if value == "edge":
        return edge
    if value == "auto":
        return auto
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def relabel(img, text, ttf_path, icon=False, fill="#ffffff", ring=None, size=12, stroke=2,
            keep_box=False, dx=0, dy=0, align="center", fit=False, name="", cut=None):
    """Replace the word in an image.

    fill, ring: "#rrggbb", or "edge" for the outline colour of the original
    lettering. With `ring`, letters get a second outline inside the first."""
    img = img.convert("RGBA")
    region = text_region(img, icon, cut)
    if region is None:
        return img
    x0, x1 = region
    px = img.load()
    rows = [y for y in range(img.height)
            if any(px[x, y][3] > OPAQUE for x in range(x0, x1))]
    centre_y = (rows[0] + rows[-1] + 1) / 2
    edge = edge_colour(img, x0, x1)
    auto = fill_colour(img, x0, x1, edge)
    fill = parse_colour(fill, edge, auto) or (255, 255, 255, 255)
    ring = parse_colour(ring, edge, auto)
    if ring:
        stroke += 1

    if align == "auto":
        # left-aligned in the picture if the old word starts near the left edge
        align = "left" if x0 + 3 < img.width - x1 else "center"
    left = x0 + 1 if icon else 1
    if align == "left" and not icon:
        left = x0
    room = img.width - 1 - left
    if fit:
        # no wider than the original word (the game lays the picture out
        # for that width): shorter words are better than squeezed ones
        room = min(room, round((x1 - x0) * 1.1) + 4)
        if align != "left" and not icon:
            left = max(1, round((x0 + x1 - room) / 2))
    if keep_box:
        # the game shows only the part of the picture where the original
        # word was: keep the new word inside that box
        left, room = x0, x1 - x0

    requested = size
    while True:
        font = ImageFont.truetype(ttf_path, size)
        box = font.getbbox(text, stroke_width=stroke)
        if box[2] - box[0] <= room or size <= (max(9, requested - 1) if fit else 9):
            break
        size -= 1
    width = box[2] - box[0]

    layer = Image.new("RGBA", (max(width, 1) + 4, img.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    # centre on the capital letters so that every word sits on the same line
    caps = font.getbbox("Н", stroke_width=stroke)
    origin = (2 - box[0], round(centre_y - (caps[1] + caps[3]) / 2))
    draw.text(origin, text, font=font, fill=edge, stroke_width=stroke, stroke_fill=edge)
    if ring:
        draw.text(origin, text, font=font, fill=ring, stroke_width=stroke - 1, stroke_fill=ring)
    draw.text(origin, text, font=font, fill=fill)
    if name and width * 0.85 > room:
        print("  tight: %s %r size %d->%d, %d px for %d" % (name, text, requested, size, width, room))
    if width > room:                      # still too long: squeeze horizontally
        layer = layer.resize((room + 4, img.height), Image.LANCZOS)
        width = room

    out = img.copy()
    out.paste((0, 0, 0, 0), (x0 if icon else 0, 0, img.width, img.height))
    if keep_box:
        pos = left - 2
    elif fit and not icon and align != "left":
        pos = round((x0 + x1 - width) / 2) - 2
    else:
        pos = left - 2 if icon or align == "left" else round((img.width - width) / 2) - 2
    out.alpha_composite(layer, (max(pos + dx, 0), dy) if dy >= 0 else (max(pos + dx, 0), 0),
                        (0, 0) if dy >= 0 else (0, -dy))
    return out


def relabel_box(img, text, ttf_path, box, fill="#ffffff", size=12, align="left",
                dx=0, dy=0, rowfill=False, outline=None, width=1, parts=(), **_ignored):
    """Replace a word that sits inside a bigger picture.

    box = [x0, y0, x1, y1]: the area of the old word. It is painted over with
    the colour found at its top-left corner, and the new word is drawn there
    without an outline, centred vertically, starting at the left edge."""
    img = img.convert("RGBA")
    x0, y0, x1, y1 = box
    background = img.getpixel((x0, y0))
    colour = parse_colour(fill, background) or (0, 0, 0, 255)
    out = img.copy()
    if rowfill == "inner":
        # a bubble with a grey rim: in every row of the box, repaint
        # everything between the rims with the colour of the box's top row
        inner = img.getpixel(((x0 + x1) // 2, y0 - 1))

        def rim(c):
            return c[3] > 100 and max(c[:3]) - min(c[:3]) < 40 and sum(c[:3]) < 690

        for y in range(y0, y1):
            xs = [x for x in range(img.width) if img.getpixel((x, y))[3] > 100]
            if not xs:
                continue
            a, b_ = xs[0], xs[-1]
            while a < b_ and rim(img.getpixel((a, y))):
                a += 1
            while b_ > a and rim(img.getpixel((b_, y))):
                b_ -= 1
            if a < b_:
                out.paste(inner, (a, y, b_ + 1, y + 1))
    elif rowfill == "outside":
        # each row of the box gets the colour that is most common in the
        # same row outside the box (and also present inside it): the band
        # or bubble the word sits on, not the letters
        for y in range(y0, y1):
            inside = {img.getpixel((x, y)) for x in range(x0, x1)}
            outside = collections.Counter(
                img.getpixel((x, y)) for x in list(range(0, x0)) + list(range(x1, img.width))
                if img.getpixel((x, y))[3] > 250)
            pick = [c for c, _ in outside.most_common() if c in inside]
            colour_y = pick[0] if pick else img.getpixel((x0, y))
            out.paste(colour_y, (x0, y, x1, y + 1))
    elif rowfill == "mode":
        # a band whose rows are each of one colour, partly covered by the
        # old word: repaint every row with its most common colour
        for y in range(y0, y1):
            row = collections.Counter(img.getpixel((x, y)) for x in range(x0, x1))
            out.paste(row.most_common(1)[0][0], (x0, y, x1, y + 1))
    elif rowfill:
        # a button with a vertical gradient: repaint every row of the box
        # with the colour that row has at the left edge of the box (or at
        # column `rowfill`, if it is a number)
        col = x0 if rowfill is True else int(rowfill)
        for y in range(y0, y1):
            out.paste(img.getpixel((col, y)), (x0, y, x1, y + 1))
    else:
        out.paste(background, (x0, y0, x1, y1))
    stroke = width if outline else 0
    edge = parse_colour(outline, background) if outline else None
    while True:
        font = ImageFont.truetype(ttf_path, size)
        bbox = font.getbbox(text, stroke_width=stroke)
        if bbox[2] - bbox[0] <= x1 - x0 or size <= 8:
            break
        size -= 1
    caps = font.getbbox("Н")
    left = x0 if align == "left" else round((x0 + x1 - (bbox[2] - bbox[0])) / 2)
    origin = (left - bbox[0] + dx, round((y0 + y1) / 2 - (caps[1] + caps[3]) / 2) + dy)
    if edge:
        ImageDraw.Draw(out).text(origin, text, font=font, fill=colour,
                                 stroke_width=stroke, stroke_fill=edge)
    else:
        ImageDraw.Draw(out).text(origin, text, font=font, fill=colour)
    for part in parts:                    # more words in the same picture
        opts = {"fill": fill, "size": size, "align": align, "rowfill": rowfill,
                "outline": outline, "width": width}
        opts.update({k: v for k, v in part.items() if k != "text"})
        out = relabel_box(out, part["text"], ttf_path, **opts)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4:
        print(__doc__)
        return 2
    labels_path, arc_name, pngdir, ttf_path = argv
    with open(labels_path, "r", encoding="utf-8") as f:
        labels = dict(json.load(f).get(arc_name, {}))
    style = {"icon": bool(labels.pop("_icon", False)),
             "fill": labels.pop("_fill", "#ffffff"),
             "ring": labels.pop("_ring", None),
             "size": int(labels.pop("_size", 12)),
             "stroke": int(labels.pop("_stroke", 2)),
             "fit": bool(labels.pop("_fit", False))}
    done = 0
    for name, text in sorted(labels.items()):
        base = os.path.join(pngdir, *name.split("/"))
        for path in (base + ".png", base + "_0.png", base + "_1.png", base + "_2.png",
                     base + "_00.png", base + "_01.png"):
            if os.path.exists(path):
                options = dict(style)
                word = text
                if isinstance(text, dict):          # per-image overrides
                    word = text["text"]
                    options.update({k: v for k, v in text.items() if k != "text"})
                if "box" in options:
                    relabel_box(Image.open(path), word, ttf_path, **options).save(path)
                else:
                    options.pop("fit", None) if options.get("keep_box") else None
                    relabel(Image.open(path), word, ttf_path, name=name, **options).save(path)
                done += 1
    print("%s: redrew %d images" % (arc_name, done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
