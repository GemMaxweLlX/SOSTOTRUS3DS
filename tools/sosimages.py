#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sosimages - take out and put back all interface textures of the game at once.

The pictures with words in them (buttons, titles, tabs) live in the SARC
archives of romfs/Layout (and its subfolders). This tool works on the whole
folder: every archive becomes a folder of PNG files, and after editing,
only the archives with changed pictures are written.

    python sosimages.py extract  rom/Layout  gfx
    python sosimages.py sheets   gfx         gfx_sheets
    python sosimages.py pack     rom/Layout  gfx  out/Layout

extract   rom/Layout/ItemComment.arc -> gfx/ItemComment/timg/*.png
          (Layout/BustUp/ch_02.arc -> gfx/BustUp/ch_02/...)
sheets    contact sheets: every picture of small size with its name, to find
          the ones with words in them quickly (several big PNG files)
pack      compares every edited PNG with the picture in the original archive and
          writes the archives that have changes to out/Layout, keeping their
          folder structure. Unchanged archives are not written. extract
          leaves a list of the files it wrote (_original.txt) so that pack
          only looks at the files that were edited since.

An edited PNG must keep its width and height. Transparency is kept. Formats
with fewer colours (RGBA4, RGB565, ETC1...) are converted back automatically,
so the result can look slightly simpler than the PNG.

Needs Pillow (pip install pillow).
"""

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sosgfx  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


MANIFEST = "_original.txt"


def digest(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def read_manifest(pngdir):
    """{png path relative to pngdir: md5} written by extract, so that pack
    only has to look at the pictures that were edited."""
    path = os.path.join(pngdir, MANIFEST)
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            md5, _, rel = line.rstrip("\n").partition(" ")
            out[rel] = md5
    return out


def archives(layout):
    for base, dirs, names in os.walk(layout):
        dirs.sort()
        for name in sorted(names):
            if name.lower().endswith(".arc"):
                path = os.path.join(base, name)
                yield path, os.path.splitext(os.path.relpath(path, layout))[0]


def images(data):
    for name, start, end in sosgfx.read_sarc(data):
        if name.endswith(".bflim"):
            yield name, start, end


def cmd_extract(args):
    arcs = done = skipped = 0
    manifest = []
    for path, rel in archives(args.layout):
        with open(path, "rb") as f:
            data = f.read()
        try:
            items = list(images(data))
        except sosgfx.FormatError as exc:
            print("пропущен %s: %s" % (rel, exc))
            continue
        if not items:
            continue
        arcs += 1
        for name, start, end in items:
            img = sosgfx.decode(data[start:end])
            if img is None:
                skipped += 1
                continue
            out = sosgfx.png_path(os.path.join(args.pngdir, rel), name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            img.save(out)
            manifest.append("%s %s" % (digest(out), os.path.relpath(out, args.pngdir)
                                       .replace("\\", "/")))
            done += 1
    with open(os.path.join(args.pngdir, MANIFEST), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest) + "\n")
    print("извлечено %d картинок из %d архивов в %s" % (done, arcs, args.pngdir))
    if skipped:
        print("%d картинок в неподдерживаемых форматах пропущено" % skipped)
    return 0


def cmd_pack(args):
    written = replaced = 0
    errors = []
    known = read_manifest(args.pngdir)
    for path, rel in archives(args.layout):
        pngdir = os.path.join(args.pngdir, rel)
        if not os.path.isdir(pngdir):
            continue
        with open(path, "rb") as f:
            data = bytearray(f.read())
        changed = []
        for name, start, end in images(bytes(data)):
            src = sosgfx.png_path(pngdir, name)
            if not os.path.exists(src):
                continue
            rel_png = os.path.relpath(src, args.pngdir).replace("\\", "/")
            if known.get(rel_png) == digest(src):
                continue                  # the file is as extract wrote it
            blob = bytes(data[start:end])
            original = sosgfx.decode(blob)
            if original is None:
                continue
            new = Image.open(src).convert("RGBA")
            if new.size == original.size and new.tobytes() == original.tobytes():
                continue
            if new.size != original.size:
                errors.append("%s: размер %dx%d, а должен быть %dx%d"
                              % (src, new.width, new.height, *original.size))
                continue
            try:
                data[start:end] = sosgfx.encode(blob, new)
            except sosgfx.FormatError as exc:
                errors.append("%s: %s" % (src, exc))
                continue
            changed.append(name)
        if changed:
            out = os.path.join(args.outdir, rel + ".arc")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)
            written += 1
            replaced += len(changed)
            if args.verbose:
                print("%s: %d" % (rel, len(changed)))
    for line in errors:
        print("ОШИБКА: " + line)
    print("заменено %d картинок, записано %d архивов в %s" % (replaced, written, args.outdir))
    return 1 if errors else 0


def cmd_sheets(args):
    items = []
    for base, dirs, names in os.walk(args.pngdir):
        dirs.sort()
        for name in sorted(names):
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(base, name)
            im = Image.open(path).convert("RGBA")
            w, h = im.size
            if w > args.max_width or h > args.max_height or im.getbbox() is None:
                continue
            items.append((os.path.relpath(path, args.pngdir), im))
    os.makedirs(args.outdir, exist_ok=True)
    width, height = 1200, 1600
    pages = []
    page = None
    x = y = rowh = 0

    def new_page():
        img = Image.new("RGBA", (width, height), (70, 90, 70, 255))
        return img, ImageDraw.Draw(img)

    page, draw = new_page()
    for name, im in items:
        cw, ch = max(im.width, 170) + 8, im.height + 14
        if x + cw > width:
            x, y, rowh = 0, y + rowh, 0
        if y + ch > height:
            pages.append(page)
            page, draw = new_page()
            x = y = rowh = 0
        page.alpha_composite(im, (x, y + 12))
        draw.text((x, y), name.replace("\\", "/").replace("/timg", ""), fill=(255, 255, 0))
        x += cw
        rowh = max(rowh, ch)
    pages.append(page)
    for i, p in enumerate(pages):
        p.convert("RGB").save(os.path.join(args.outdir, "sheet_%03d.png" % (i + 1)))
    print("%d картинок на %d листах в %s" % (len(items), len(pages), args.outdir))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Извлечение и вставка текстур интерфейса Story of Seasons: Trio of Towns")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract", help="все архивы папки Layout -> PNG")
    p.add_argument("layout", help="папка romfs/Layout из игры")
    p.add_argument("pngdir", help="куда сложить PNG")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("pack", help="изменённые PNG -> архивы .arc")
    p.add_argument("layout", help="оригинальная папка romfs/Layout")
    p.add_argument("pngdir", help="папка с PNG (после extract и правки)")
    p.add_argument("outdir", help="куда записать изменённые архивы")
    p.add_argument("-v", "--verbose", action="store_true", help="перечислить архивы")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("sheets", help="обзорные листы для поиска картинок с текстом")
    p.add_argument("pngdir")
    p.add_argument("outdir")
    p.add_argument("--max-width", type=int, default=512)
    p.add_argument("--max-height", type=int, default=128)
    p.set_defaults(func=cmd_sheets)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (sosgfx.FormatError, OSError) as exc:
        print("ошибка: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
