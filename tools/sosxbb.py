#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sosxbb - text tool for Story of Seasons: Trio of Towns (3DS, USA version).

Works with romfs/Msg.xbb and romfs/DataText.xbb. Only the Python standard
library is needed (Python 3.8+).

    python sosxbb.py verify  rom/Msg.xbb
    python sosxbb.py unpack  rom/Msg.xbb       text/Msg
    python sosxbb.py fill    text/Msg
    python sosxbb.py check   text/Msg --font rom/mainfont.bffnt
    python sosxbb.py stats   text/Msg
    python sosxbb.py pack    rom/Msg.xbb       text/Msg   out/Msg.xbb
    python sosxbb.py save    text/Msg          translation/Msg
    python sosxbb.py load    translation/Msg   text/Msg

File formats (all little endian)

XBB archive
    0x00  "XBB\\x01"
    0x04  u32 file count N
    0x08  zero padding up to 0x20
    0x20  N entries of 16 bytes: data offset, data size, name offset, name hash
          N entries of 8 bytes:  name hash, entry index (sorted by hash)
          N zero-terminated file names
          zero padding to a multiple of 4
          file data, back to back, every size is a multiple of 4
    Files inside Msg.xbb are PAPA tables. Files inside DataText.xbb are
    XBB archives which in turn hold PAPA tables.

PAPA table
    0x00  "PAPA"
    0x04  u32 0
    0x08  u32 12
    0x0C  u32 8 + 4 * B
    0x10  u32 block count B (the last block is 8 zero bytes)
    0x14  B block offsets
    block: u32 block size, u32 field count F, F field offsets (relative to
           the block start), then the fields.
    Field 0 is an ASCII stamp ("Msg" or "DataText"). In Msg.xbb field 1 is
    the message label (ASCII / Shift-JIS) and field 2 is the text. In
    DataText.xbb fields 1..5 are texts. Texts are UTF-16LE, terminated by
    a zero character and padded with zero bytes to a multiple of 4.
"""

import argparse
import json
import os
import re
import struct
import sys
import zlib

XBB_MAGIC = b"XBB\x01"
PAPA_MAGIC = b"PAPA"
TAG_RE = re.compile(r"<[^<>\n]*>")
LANG = "ru"


class FormatError(Exception):
    pass


# --------------------------------------------------------------------------
# XBB
# --------------------------------------------------------------------------

class Xbb:
    """Parsed XBB archive. `head` is everything before the first file."""

    def __init__(self, data):
        if data[:4] != XBB_MAGIC:
            raise FormatError("not an XBB archive (magic %r)" % data[:4])
        count = struct.unpack_from("<I", data, 4)[0]
        self.count = count
        self.entries = [struct.unpack_from("<IIII", data, 0x20 + 16 * i)
                        for i in range(count)]
        self.names = []
        for _off, _size, name_off, _hash in self.entries:
            end = data.index(b"\0", name_off)
            self.names.append(data[name_off:end].decode("ascii"))
        self.files = [data[off:off + size]
                      for off, size, _n, _h in self.entries]
        if count:
            first = self.entries[0][0]
            pos = first
            for off, size, _n, _h in self.entries:
                if off != pos:
                    raise FormatError("XBB data is not stored back to back")
                pos += size
            if pos != len(data):
                raise FormatError("unexpected bytes after the last XBB file")
            self.head = data[:first]
        else:
            self.head = data

    def build(self, files):
        """Rebuild the archive with new file contents (same order/names)."""
        if len(files) != self.count:
            raise FormatError("file count changed")
        out = bytearray(self.head)
        pos = len(out)
        for i, blob in enumerate(files):
            if len(blob) % 4:
                raise FormatError("file size is not a multiple of 4")
            _off, _size, name_off, name_hash = self.entries[i]
            struct.pack_into("<IIII", out, 0x20 + 16 * i,
                             pos, len(blob), name_off, name_hash)
            pos += len(blob)
        for blob in files:
            out += blob
        return bytes(out)


# --------------------------------------------------------------------------
# PAPA
# --------------------------------------------------------------------------

def _pad4(b):
    return b + b"\0" * (-len(b) % 4)


def encode_text(text):
    return _pad4(text.encode("utf-16-le") + b"\0\0")


def decode_text(raw):
    """Decode a UTF-16LE field. Returns None if it is not a clean text."""
    if len(raw) % 2:
        return None
    end = 0
    while end < len(raw) and raw[end:end + 2] != b"\0\0":
        end += 2
    try:
        text = raw[:end].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    if encode_text(text) != raw:
        return None
    return text


def decode_label(raw):
    body = raw.rstrip(b"\0")
    try:
        return body.decode("cp932")
    except UnicodeDecodeError:
        return body.decode("latin-1")


class Papa:
    """Parsed PAPA table: a list of rows, each row a list of raw fields."""

    def __init__(self, data):
        if data[:4] != PAPA_MAGIC:
            raise FormatError("not a PAPA table (magic %r)" % data[:4])
        self.unk4, self.unk8, _hsize, count = struct.unpack_from("<IIII", data, 4)
        offs = list(struct.unpack_from("<%dI" % count, data, 0x14))
        offs.append(len(data))
        self.rows = []
        self.tail = b""
        for i in range(count):
            block = data[offs[i]:offs[i + 1]]
            if i == count - 1:
                self.tail = block          # terminating dummy block
                break
            _bsize, nfields = struct.unpack_from("<II", block, 0)
            foffs = list(struct.unpack_from("<%dI" % nfields, block, 8))
            foffs.append(len(block))
            self.rows.append([block[foffs[j]:foffs[j + 1]]
                              for j in range(nfields)])

    def build(self):
        blocks = []
        for fields in self.rows:
            n = len(fields)
            pos = 8 + 4 * n
            foffs = []
            for f in fields:
                foffs.append(pos)
                pos += len(f)
            blocks.append(struct.pack("<II%dI" % n, pos, n, *foffs)
                          + b"".join(fields))
        blocks.append(self.tail)
        count = len(blocks)
        pos = 0x14 + 4 * count
        offs = []
        for b in blocks:
            offs.append(pos)
            pos += len(b)
        head = PAPA_MAGIC + struct.pack("<IIII%dI" % count, self.unk4,
                                        self.unk8, 8 + 4 * count, count, *offs)
        return head + b"".join(blocks)

    def text_slots(self):
        """Yield (row, field, label, text) for every translatable field."""
        for r, fields in enumerate(self.rows):
            if not fields:
                continue                    # empty row (seen in the update)
            stamp = fields[0].rstrip(b"\0")
            if stamp == b"Msg":
                # field 1 is the label; rows without field 2 have no text
                if len(fields) >= 3:
                    text = decode_text(fields[2])
                    if text is not None:
                        yield r, 2, decode_label(fields[1]), text
            elif stamp == b"DataText":
                for f in range(1, len(fields)):
                    text = decode_text(fields[f])
                    # "-" marks an unused column in DataText tables
                    if text is not None and text != "-":
                        yield r, f, "", text


# --------------------------------------------------------------------------
# walking an archive (Msg.xbb is flat, DataText.xbb is nested)
# --------------------------------------------------------------------------

def walk(data, prefix=""):
    """Yield (relative path, Papa) for every table inside an XBB archive."""
    xbb = Xbb(data)
    for name, blob in zip(xbb.names, xbb.files):
        path = prefix + name
        if blob[:4] == XBB_MAGIC:
            yield from walk(blob, path + "/")
        elif blob[:4] == PAPA_MAGIC:
            yield path, Papa(blob)


def rebuild(data, replace, prefix=""):
    """Rebuild an archive. `replace(path, papa)` may edit papa.rows."""
    xbb = Xbb(data)
    files = []
    for name, blob in zip(xbb.names, xbb.files):
        path = prefix + name
        if blob[:4] == XBB_MAGIC:
            files.append(rebuild(blob, replace, path + "/"))
        elif blob[:4] == PAPA_MAGIC:
            papa = Papa(blob)
            replace(path, papa)
            files.append(papa.build())
        else:
            files.append(blob)
    return xbb.build(files)


def json_path(root, path):
    return os.path.join(root, *path.split("/")) + ".json"


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def iter_json(root):
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            if name.endswith(".json"):
                yield os.path.join(base, name)


# --------------------------------------------------------------------------
# font (optional, for `check --font`)
# --------------------------------------------------------------------------

def load_font_widths(path):
    """Return {character: advance width in pixels} from a BFFNT font."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"FFNT" or d[4:6] != b"\xff\xfe":
        raise FormatError("not a little-endian BFFNT font")
    pos = struct.unpack_from("<H", d, 6)[0]
    cmap = {}
    widths = []
    while pos + 8 <= len(d):
        magic = d[pos:pos + 4]
        size = struct.unpack_from("<I", d, pos + 4)[0]
        if size < 8:
            break
        body = d[pos + 8:pos + size]
        if magic == b"CWDH":
            start, end = struct.unpack_from("<HH", body, 0)
            for i in range(end - start + 1):
                _left, _glyph, advance = struct.unpack_from("<bBB", body, 8 + 3 * i)
                while len(widths) <= start + i:
                    widths.append(0)
                widths[start + i] = advance
        elif magic == b"CMAP":
            first, last, method = struct.unpack_from("<HHH", body, 0)
            if method == 0:
                base = struct.unpack_from("<H", body, 12)[0]
                for c in range(first, last + 1):
                    cmap[c] = base + c - first
            elif method == 1:
                for i in range(last - first + 1):
                    idx = struct.unpack_from("<H", body, 12 + 2 * i)[0]
                    if idx != 0xFFFF:
                        cmap[first + i] = idx
            else:
                n = struct.unpack_from("<H", body, 12)[0]
                for i in range(n):
                    c, idx = struct.unpack_from("<HH", body, 14 + 4 * i)
                    cmap[c] = idx
        pos += size
    return {chr(c): widths[i] for c, i in cmap.items() if i < len(widths)}


def visible_lines(text):
    """Text split into lines with control tags removed."""
    plain = TAG_RE.sub("", text.replace("\r", ""))
    return plain.split("\n")


def page_lines(text):
    """Largest number of non-empty lines between two <PAGE> tags."""
    return max(sum(1 for line in visible_lines(page) if line)
               for page in text.split("<PAGE>"))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_verify(args):
    with open(args.xbb, "rb") as f:
        data = f.read()
    tables = rows = slots = 0
    for _path, papa in walk(data):
        tables += 1
        rows += len(papa.rows)
        slots += sum(1 for _ in papa.text_slots())
    again = rebuild(data, lambda path, papa: None)
    if again != data:
        print("FAILED: the rebuilt archive differs from the original.")
        print("This file is not supported by this tool as is.")
        return 1
    print("OK: rebuilt archive is byte-identical to the original.")
    print("tables: %d, rows: %d, translatable texts: %d" % (tables, rows, slots))
    return 0


def cmd_unpack(args):
    with open(args.xbb, "rb") as f:
        data = f.read()
    written = kept = total = 0
    for path, papa in walk(data):
        entries = []
        for r, fld, label, text in papa.text_slots():
            entry = {"n": r, "f": fld}
            if label:
                entry["id"] = label
            entry["en"] = text
            entry[LANG] = ""
            entries.append(entry)
        if not entries:
            continue
        total += len(entries)
        out = json_path(args.outdir, path)
        if os.path.exists(out) and not args.force:
            # keep translations that are already there
            old = {(e["n"], e["f"]): e for e in read_json(out)["entries"]}
            for e in entries:
                prev = old.get((e["n"], e["f"]))
                if prev and prev.get("en") == e["en"] and prev.get(LANG):
                    e[LANG] = prev[LANG]
                    kept += 1
        write_json(out, {"file": path, "entries": entries})
        written += 1
    print("wrote %d files with %d texts to %s" % (written, total, args.outdir))
    if kept:
        print("kept %d existing translations" % kept)
    return 0


def cmd_pack(args):
    with open(args.xbb, "rb") as f:
        data = f.read()
    counters = {"done": 0, "files": 0}
    errors = []

    def replace(path, papa):
        src = json_path(args.textdir, path)
        if not os.path.exists(src):
            return
        counters["files"] += 1
        for e in read_json(src)["entries"]:
            new = e.get(LANG) or ""
            if not new:
                continue
            r, fld = e["n"], e["f"]
            try:
                original = decode_text(papa.rows[r][fld])
            except IndexError:
                original = None
            if original != e.get("en"):
                errors.append("%s: entry n=%s f=%s does not match the original "
                              "archive (other game version?)" % (path, r, fld))
                continue
            try:
                papa.rows[r][fld] = encode_text(new)
            except UnicodeEncodeError:
                errors.append("%s: entry n=%s f=%s cannot be encoded" % (path, r, fld))
                continue
            counters["done"] += 1

    out = rebuild(data, replace)
    if errors:
        for line in errors[:20]:
            print("ERROR: " + line)
        if len(errors) > 20:
            print("... and %d more" % (len(errors) - 20))
        print("nothing written")
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(out)
    print("applied %d translations from %d files" % (counters["done"], counters["files"]))
    print("wrote %s (%d bytes, original %d bytes)" % (args.out, len(out), len(data)))
    return 0


def cmd_stats(args):
    total = done = chars = chars_done = 0
    rows = []
    for path in iter_json(args.textdir):
        entries = read_json(path)["entries"]
        t = len(entries)
        d = sum(1 for e in entries if e.get(LANG))
        total += t
        done += d
        chars += sum(len(e["en"]) for e in entries)
        chars_done += sum(len(e["en"]) for e in entries if e.get(LANG))
        rows.append((os.path.relpath(path, args.textdir), d, t))
    if args.files:
        for name, d, t in rows:
            print("%6d / %-6d %s" % (d, t, name))
    pct = 100.0 * done / total if total else 0.0
    cpct = 100.0 * chars_done / chars if chars else 0.0
    print("files: %d" % len(rows))
    print("texts: %d / %d translated (%.1f%%)" % (done, total, pct))
    print("characters of original text: %d / %d (%.1f%%)" % (chars_done, chars, cpct))
    return 0


def cmd_fill(args):
    """Copy translations to untranslated entries with identical original text."""
    memory = {}
    conflicts = set()
    docs = []
    for path in iter_json(args.textdir):
        doc = read_json(path)
        docs.append((path, doc))
        for e in doc["entries"]:
            if e.get(LANG):
                if e["en"] in memory and memory[e["en"]] != e[LANG]:
                    conflicts.add(e["en"])
                memory.setdefault(e["en"], e[LANG])
    filled = changed_files = 0
    for path, doc in docs:
        changed = False
        for e in doc["entries"]:
            if not e.get(LANG) and e["en"] in memory and e["en"] not in conflicts:
                e[LANG] = memory[e["en"]]
                filled += 1
                changed = True
        if changed:
            write_json(path, doc)
            changed_files += 1
    print("filled %d entries in %d files" % (filled, changed_files))
    if conflicts:
        print("skipped %d texts that have more than one translation" % len(conflicts))
    return 0


def cmd_check(args):
    widths = load_font_widths(args.font) if args.font else None
    problems = 0
    checked = 0

    def report(path, e, message):
        nonlocal problems
        problems += 1
        if problems <= args.limit:
            print("%s  n=%s %s: %s" % (path, e["n"], e.get("id", ""), message))

    for path in iter_json(args.textdir):
        rel = os.path.relpath(path, args.textdir)
        for e in read_json(path)["entries"]:
            new = e.get(LANG) or ""
            if not new:
                continue
            checked += 1
            old = e["en"]
            if sorted(TAG_RE.findall(old)) != sorted(TAG_RE.findall(new)):
                report(rel, e, "control tags differ from the original")
            if old.count("\r") != new.count("\r"):
                report(rel, e, "number of \\r characters differs from the original")
            if "\0" in new:
                report(rel, e, "contains a zero character")
            if widths is not None:
                missing = sorted({c for line in visible_lines(new) for c in line
                                  if c not in widths})
                if missing:
                    report(rel, e, "characters missing in the font: %s"
                           % " ".join("%s(U+%04X)" % (c, ord(c)) for c in missing))
                limit = max(sum(widths.get(c, 0) for c in line)
                            for line in visible_lines(old))
                for line in visible_lines(new):
                    w = sum(widths.get(c, 0) for c in line)
                    if w > max(limit, args.max_width):
                        report(rel, e, "line is %d px wide (limit %d px): %s"
                               % (w, max(limit, args.max_width), line))
                if page_lines(new) > max(page_lines(old), args.max_lines):
                    report(rel, e, "%d lines on one page (limit %d)"
                           % (page_lines(new), max(page_lines(old), args.max_lines)))
            elif len(visible_lines(new)) != len(visible_lines(old)) and args.lines:
                report(rel, e, "number of lines differs from the original")
    print("checked %d translated texts, %d problems" % (checked, problems))
    if problems > args.limit:
        print("(only the first %d are shown, use --limit to see more)" % args.limit)
    return 1 if problems else 0


def text_key(text):
    """Checksum of an original text, stored instead of the text itself."""
    return "%08x" % (zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF)


def cmd_save(args):
    """Write translations without the original text (safe to publish)."""
    files = count = 0
    for path in iter_json(args.textdir):
        doc = read_json(path)
        entries = []
        for e in doc["entries"]:
            if e.get(LANG):
                item = {"n": e["n"], "f": e["f"]}
                if "id" in e:
                    item["id"] = e["id"]
                item["key"] = text_key(e["en"])
                item[LANG] = e[LANG]
                entries.append(item)
        out = os.path.join(args.outdir, os.path.relpath(path, args.textdir))
        if entries:
            write_json(out, {"file": doc["file"], "entries": entries})
            files += 1
            count += len(entries)
        elif os.path.exists(out):
            os.remove(out)
    print("saved %d translations in %d files to %s" % (count, files, args.outdir))
    return 0


def cmd_load(args):
    """Merge saved translations into unpacked text files."""
    loaded = stale = files = 0
    for path in iter_json(args.savedir):
        target = os.path.join(args.textdir, os.path.relpath(path, args.savedir))
        if not os.path.exists(target):
            print("no unpacked file for %s (run unpack first)" % path)
            stale += len(read_json(path)["entries"])
            continue
        doc = read_json(target)
        index = {(e["n"], e["f"]): e for e in doc["entries"]}
        # fallback for other game versions where rows have moved:
        # same label and same original text anywhere in the file
        moved = {}
        for e in doc["entries"]:
            moved.setdefault((e.get("id"), text_key(e["en"])), e)
        changed = False
        for item in read_json(path)["entries"]:
            e = index.get((item["n"], item["f"]))
            if e is None or text_key(e["en"]) != item.get("key"):
                e = moved.get((item.get("id"), item.get("key")))
            if e is None:
                stale += 1
                continue
            if e.get(LANG) != item[LANG]:
                e[LANG] = item[LANG]
                changed = True
            loaded += 1
        if changed:
            write_json(target, doc)
            files += 1
    print("loaded %d translations, updated %d files" % (loaded, files))
    if stale:
        print("%d saved translations do not match this game version and were skipped" % stale)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Text tool for Story of Seasons: Trio of Towns (3DS)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("verify", help="check that an .xbb can be rebuilt byte for byte")
    p.add_argument("xbb")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("unpack", help="extract texts from an .xbb into JSON files")
    p.add_argument("xbb")
    p.add_argument("outdir")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing JSON files and drop their translations")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("pack", help="build a translated .xbb")
    p.add_argument("xbb", help="original .xbb from the game")
    p.add_argument("textdir", help="directory with translated JSON files")
    p.add_argument("out", help="output .xbb")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("stats", help="show translation progress")
    p.add_argument("textdir")
    p.add_argument("--files", action="store_true", help="list every file")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("fill", help="reuse translations for identical original texts")
    p.add_argument("textdir")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("check", help="look for broken tags, missing glyphs, long lines")
    p.add_argument("textdir")
    p.add_argument("--font", help="mainfont.bffnt, enables glyph and width checks")
    p.add_argument("--max-width", type=int, default=280,
                   help="line width limit in pixels (default 280, the width "
                        "of the longest dialogue lines in the original)")
    p.add_argument("--max-lines", type=int, default=3,
                   help="lines per page limit (default 3)")
    p.add_argument("--lines", action="store_true",
                   help="without --font: report a changed number of lines")
    p.add_argument("--limit", type=int, default=50, help="problems to print")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("save", help="store translations without the original text")
    p.add_argument("textdir", help="directory with unpacked JSON files")
    p.add_argument("outdir", help="directory for translation-only files")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("load", help="merge stored translations into unpacked files")
    p.add_argument("savedir", help="directory with translation-only files")
    p.add_argument("textdir", help="directory with unpacked JSON files")
    p.set_defaults(func=cmd_load)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (FormatError, OSError, json.JSONDecodeError, KeyError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
