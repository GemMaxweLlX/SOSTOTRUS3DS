#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sosart - draw the few decorative title pictures that are lettering art
rather than plain labels, in the style of the originals:

  Trophy.arc       timg/co    "Congratulations!" on an arc, letters shaded
                              from yellow at the ends to red in the middle
  Competition.arc  timg/time  "TIME": every letter its own colour, lighter
                   timg/out   inside, with a white and a dark outline ("UP!")

    python sosart.py PNGROOT Nunito[wght].ttf

PNGROOT holds the pictures extracted by sosgfx.py, one folder per archive
(PNGROOT/Trophy/timg/co.png ...). The pictures are redrawn in place at the
size of the originals. Needs Pillow.
"""

import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SS = 4                       # supersampling factor

CONGRATS = "Поздравляем!"
TIME = ("ВРЕМЯ", ["purple", "green", "red", "orange", "blue"])
UP = ("ВЫШЛО!", ["lime", "orange", "teal", "purple", "red", "teal"])

# (edge colour, inner colour) of a letter, sampled from the originals
PALETTE = {
    "purple": ((153, 17, 119), (204, 102, 187)),
    "green": ((0, 136, 0), (34, 204, 85)),
    "red": ((255, 34, 17), (255, 136, 136)),
    "orange": ((255, 136, 0), (255, 214, 34)),
    "blue": ((17, 85, 204), (102, 170, 255)),
    "lime": ((136, 187, 0), (204, 238, 51)),
    "teal": ((0, 102, 102), (34, 170, 170)),
}


def font(path, size, weight=b"Black"):
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_name(weight)
    except (OSError, ValueError):
        pass
    return f


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def finish(big, size):
    """Downscale the supersampled picture and drop faint fringes."""
    img = big.resize(size, Image.LANCZOS)
    alpha = img.getchannel("A").point(lambda v: 0 if v < 40 else (255 if v > 200 else v))
    img.putalpha(alpha)
    return img


def congrats(ttf, size=(256, 64)):
    w, h = size[0] * SS, size[1] * SS
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # baseline arc through roughly the same points as the English word
    cx, apex, ends = w / 2, 32 * SS, 52 * SS
    half = (size[0] / 2 - 6) * SS
    radius = (half ** 2 + (ends - apex) ** 2) / (2 * (ends - apex))
    cy = apex + radius

    px = 40
    while True:
        f = font(ttf, px * SS)
        adv = [f.getlength(ch) for ch in CONGRATS]
        total = sum(adv) * 0.94
        if total <= 2 * half or px <= 12:
            break
        px -= 1
    stroke = 2 * SS
    pos = -total / 2
    for ch, a in zip(CONGRATS, adv):
        a *= 0.94                                  # letters sit close together
        s = pos + a / 2                            # arc length of the letter centre
        pos += a
        ang = s / radius
        x = cx + radius * math.sin(ang)
        y = cy - radius * math.cos(ang)
        t = min(1.0, abs(x - cx) / half)
        base = lerp((236, 24, 0), (250, 120, 0), min(t / 0.55, 1)) if t < 0.55 else \
            lerp((250, 120, 0), (255, 214, 20), (t - 0.55) / 0.45)

        box = f.getbbox(ch, stroke_width=stroke)
        lw, lh = box[2] - box[0] + 4 * SS, box[3] - box[1] + 4 * SS
        org = (2 * SS - box[0], 2 * SS - box[1])
        layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text(org, ch, font=f, fill=(117, 100, 49, 255), stroke_width=stroke,
               stroke_fill=(117, 100, 49, 255))
        mask = Image.new("L", (lw, lh), 0)
        ImageDraw.Draw(mask).text(org, ch, font=f, fill=255)
        # lighter top, darker bottom, like the original letters
        shade = Image.new("RGBA", (lw, lh))
        for yy in range(lh):
            k = yy / max(lh - 1, 1)
            c = lerp(lerp(base, (255, 240, 150), 0.45), base, min(k * 1.6, 1)) if k < 0.62 else \
                lerp(base, lerp(base, (120, 20, 0), 0.35), (k - 0.62) / 0.38)
            ImageDraw.Draw(shade).line([(0, yy), (lw, yy)], fill=c + (255,))
        layer.paste(shade, (0, 0), mask)
        layer = layer.rotate(-math.degrees(ang), resample=Image.BICUBIC, expand=True)
        # place the letter so that its baseline centre sits on the arc
        bx = x - layer.width / 2
        by = y - layer.height / 2 - (f.getbbox("Н")[3] - f.getbbox("Н")[1]) * 0.45
        out.alpha_composite(layer, (round(bx), round(by)))
    return finish(out, size)


def fit_px(ttf, text, room, top=6, bottom=58):
    """Largest font size at which `text` with its rims fits `room` pixels."""
    dark = 6 * SS
    px = 70
    while px > 20:
        box = font(ttf, px * SS).getbbox(text, stroke_width=dark)
        if box[3] - box[1] <= (bottom - top) * SS and box[2] - box[0] <= room * SS:
            break
        px -= 1
    return px


def letters(ttf, text, colours, size, align, px, top=6, bottom=58):
    w, h = size[0] * SS, size[1] * SS
    white, dark = 4 * SS, 6 * SS
    f = font(ttf, px * SS)
    box = f.getbbox(text, stroke_width=dark)
    width = box[2] - box[0]
    x0 = (size[0] - 2) * SS - width if align == "right" else 1 * SS
    org = (x0 - box[0], top * SS - box[1] + ((bottom - top) * SS - (box[3] - box[1])) // 2)

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    xs = []
    x = org[0]
    for ch in text:
        xs.append(x)
        x += f.getlength(ch)
    for ch, x in zip(text, xs):           # dark rim under everything
        d.text((x, org[1]), ch, font=f, fill=(34, 34, 34, 255), stroke_width=dark,
               stroke_fill=(34, 34, 34, 255))
    for ch, x in zip(text, xs):           # white rim
        d.text((x, org[1]), ch, font=f, fill=(255, 255, 238, 255), stroke_width=white,
               stroke_fill=(255, 255, 238, 255))
    for ch, x, name in zip(text, xs, colours):
        edge, inner = PALETTE[name]
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).text((x, org[1]), ch, font=f, fill=255)
        glow = mask.filter(ImageFilter.MinFilter(4 * SS + 1)).filter(ImageFilter.GaussianBlur(3 * SS))
        letter = Image.new("RGBA", (w, h), edge + (255,))
        letter.paste(Image.new("RGBA", (w, h), inner + (255,)), (0, 0), glow)
        out.paste(letter, (0, 0), mask)
    return finish(out, size)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    root, ttf = argv
    # The layout puts "time" left of the screen centre and "out" right of
    # it; only the first 182 pixels of "out" are on screen. Both words get
    # the same letter size, the largest at which they stay visible.
    px = min(fit_px(ttf, TIME[0], 214), fit_px(ttf, UP[0], 180))
    jobs = {
        ("Trophy", "co"): lambda s: congrats(ttf, s),
        ("Competition", "time"): lambda s: letters(ttf, TIME[0], TIME[1], s, "right", px),
        ("Competition", "out"): lambda s: letters(ttf, UP[0], UP[1], s, "left", px),
    }
    for (arc, name), make in jobs.items():
        path = os.path.join(root, arc, "timg", name + ".png")
        if not os.path.exists(path):
            continue
        size = Image.open(path).size
        make(size).save(path)
        print("drew %s/%s" % (arc, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
