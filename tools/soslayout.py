#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soslayout - replace the fixed words that layouts (BFLYT inside Layout/*.arc)
show in their text boxes, such as "Weight" or "Year" on some screens.

    python soslayout.py translation/gfx/layout_text.json rom/Layout out/Layout

The JSON maps an archive name to {pane name: text}. A text box has a fixed
buffer in the file; the new text must fit into it (the tool reports the
limit). The archive is edited in place and keeps its size.
"""

import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sosgfx  # noqa: E402


def text_panes(data, start, end):
    """Yield (pane name, absolute offset of txt1 section) inside a BFLYT."""
    pos = start + struct.unpack_from("<H", data, start + 6)[0]
    while pos + 8 <= end:
        magic = bytes(data[pos:pos + 4])
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if size < 8:
            break
        if magic == b"txt1":
            name = bytes(data[pos + 0x0C:pos + 0x24]).split(b"\0")[0].decode("ascii", "replace")
            yield name, pos
        pos += size


def set_text(data, sec, text):
    buf_len = struct.unpack_from("<H", data, sec + 0x54)[0]
    text_off = struct.unpack_from("<I", data, sec + 0x64)[0]
    raw = text.encode("utf-16-le") + b"\0\0"
    if len(raw) > buf_len:
        raise ValueError("%r needs %d bytes, the box holds %d (%d characters)"
                         % (text, len(raw), buf_len, buf_len // 2 - 1))
    data[sec + text_off:sec + text_off + buf_len] = raw + b"\0" * (buf_len - len(raw))
    struct.pack_into("<H", data, sec + 0x56, len(raw))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print(__doc__)
        return 2
    table_path, src_dir, out_dir = argv
    with open(table_path, "r", encoding="utf-8") as f:
        table = json.load(f)
    errors = 0
    for arc, panes in sorted(table.items()):
        with open(os.path.join(src_dir, arc), "rb") as f:
            data = bytearray(f.read())
        done = set()
        for name, start, end in sosgfx.read_sarc(data):
            if not name.endswith(".bflyt"):
                continue
            for pane, sec in list(text_panes(data, start, end)):
                if pane in panes:
                    try:
                        set_text(data, sec, panes[pane])
                        done.add(pane)
                    except ValueError as exc:
                        print("%s/%s: %s" % (arc, pane, exc))
                        errors += 1
        missing = sorted(set(panes) - done)
        if missing:
            print("%s: panes not found: %s" % (arc, ", ".join(missing)))
            errors += 1
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, arc), "wb") as f:
            f.write(data)
        print("%s: %d text boxes" % (arc, len(done)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
