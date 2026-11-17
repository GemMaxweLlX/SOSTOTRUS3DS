#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sostable - translate the game texts in a spreadsheet.

Turns the JSON files made by `sosxbb.py unpack` into one table with the
original text next to an empty (or already filled) translation column, and
puts the translations from that table back into the JSON files.

    python sostable.py export text/Msg  table.xlsx
    python sostable.py export text/Msg  table.xlsx --untranslated --files "Talk_*"
    python sostable.py import table.xlsx text/Msg --font rom/mainfont.bffnt

The table can be .xlsx (Excel, LibreOffice, Google Sheets; needs the
openpyxl package: pip install openpyxl) or .csv (UTF-8, separator ";").

Columns:
    Файл        name of the game file the text belongs to
    №, Поле     position of the text in that file
    ID          the game's label for the text (may be empty)
    Служебное   portrait and speaker codes before the text; not translated,
                they are put back automatically
    Оригинал    the English text
    Перевод     the translation: type it here
    Заметки     free column for the translator; ignored on import

Rules for the Перевод column:
  * keep every tag in angle brackets exactly as in the original:
    <PAGE> (new page), <_____MYNAME_____> (player name), <BLUE>...</BLUE>,
    <MOJI0> and so on;
  * line breaks inside the cell are line breaks in the game;
  * an empty cell means "no translation yet" and changes nothing;
  * {U+0001} and the like stand for invisible control characters of the
    original: leave them where they are.

`import` checks every translated row (tags, line width and lines per page
with --font) and prints the problems; rows with broken tags are not taken.
"""

import argparse
import csv
import fnmatch
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sosxbb  # noqa: E402

HEADER = ["Файл", "№", "Поле", "ID", "Служебное", "Оригинал", "Перевод", "Заметки"]
LANG = sosxbb.LANG


def split_head(text):
    """'<FACE_..>\\r\\nHello' -> ('<FACE_..>', 'Hello'); plain text -> ('', text)."""
    if "\r\n" in text:
        head, body = text.split("\r\n", 1)
        return head, body
    return "", text


def join_head(head, body):
    return head + "\r\n" + body if head else body


CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f]")
ESCAPED = re.compile(r"\{U\+([0-9A-Fa-f]{4})\}")


def escape(text):
    """Control characters (other than line breaks) cannot be stored in a
    spreadsheet: write them as {U+0001} and so on."""
    return CONTROL.sub(lambda m: "{U+%04X}" % ord(m.group()), text)


def unescape(text):
    return ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), text)


def cell_text(value):
    """Spreadsheets give back \\r\\n or \\r for line breaks: normalise to \\n."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.replace("\r\n", "\n").replace("\r", "\n")


# --------------------------------------------------------------------------
# reading and writing tables
# --------------------------------------------------------------------------

def need_openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise SystemExit("Для файлов .xlsx нужен пакет openpyxl: pip install openpyxl\n"
                         "(или сохраните таблицу как .csv)")


def write_table(path, rows):
    if path.lower().endswith(".csv"):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(HEADER)
            w.writerows(rows)
        return
    need_openpyxl()
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation  # noqa: F401

    wb = Workbook()
    ws = wb.active
    ws.title = "Перевод"
    ws.append(HEADER)
    for row in rows:
        ws.append(row)
    grey = PatternFill("solid", fgColor="EEEEEE")
    yellow = PatternFill("solid", fgColor="FFF6CC")
    wrap = Alignment(wrap_text=True, vertical="top")
    for cell in ws[1]:
        cell.font = Font(bold=True)
    widths = {"A": 24, "B": 6, "C": 5, "D": 26, "E": 18, "F": 60, "G": 60, "H": 30}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for r in ws.iter_rows(min_row=2):
        for cell in r:
            cell.alignment = wrap
        for i in (0, 1, 2, 3, 4, 5):
            r[i].fill = grey
        r[6].fill = yellow
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def read_table(path):
    """Yield dicts with the HEADER names as keys."""
    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            delim = ";" if sample.count(";") >= sample.count(",") else ","
            reader = csv.reader(f, delimiter=delim)
            names = next(reader)
            for values in reader:
                yield dict(zip(names, values))
        return
    need_openpyxl()
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    names = [cell_text(v) for v in next(rows)]
    for values in rows:
        yield dict(zip(names, values))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_export(args):
    rows = []
    files = 0
    for path in sosxbb.iter_json(args.textdir):
        doc = sosxbb.read_json(path)
        name = doc.get("file") or os.path.splitext(os.path.relpath(path, args.textdir))[0]
        if args.files and not any(fnmatch.fnmatch(name, p) for p in args.files):
            continue
        added = False
        for e in doc["entries"]:
            if args.untranslated and e.get(LANG):
                continue
            if not e["en"].strip() or e["en"] == "NoMsg":
                continue
            head, en = split_head(e["en"])
            _h, ru = split_head(e.get(LANG) or "")
            rows.append([name, e["n"], e["f"], escape(e.get("id", "")), escape(head),
                         escape(en), escape(ru), ""])
            added = True
        files += added
    write_table(args.table, rows)
    print("в таблицу %s записано %d строк из %d файлов" % (args.table, len(rows), files))
    return 0


def cmd_import(args):
    widths = sosxbb.load_font_widths(args.font) if args.font else None
    docs = {}
    for path in sosxbb.iter_json(args.textdir):
        doc = sosxbb.read_json(path)
        name = doc.get("file") or os.path.splitext(os.path.relpath(path, args.textdir))[0]
        docs[name] = (path, doc, {(e["n"], e["f"]): e for e in doc["entries"]})

    taken = same = skipped = problems = 0
    changed = set()

    def warn(row, message):
        nonlocal problems
        problems += 1
        if problems <= args.limit:
            print("строка %d (%s №%s): %s" % (row, file_name, number, message))

    for row, item in enumerate(read_table(args.table), start=2):
        file_name = cell_text(item.get("Файл")).strip()
        if not file_name:
            continue
        number = cell_text(item.get("№")).strip()
        try:
            key = (int(float(number)), int(float(cell_text(item.get("Поле")) or 2)))
        except ValueError:
            warn(row, "не число в столбце № или Поле")
            skipped += 1
            continue
        ru = unescape(cell_text(item.get("Перевод")))
        if not ru.strip():
            continue
        if file_name not in docs:
            warn(row, "нет такого файла в %s" % args.textdir)
            skipped += 1
            continue
        path, doc, index = docs[file_name]
        e = index.get(key)
        if e is None:
            warn(row, "нет такой строки в файле")
            skipped += 1
            continue
        head, en = split_head(e["en"])
        if unescape(cell_text(item.get("Оригинал"))) != en:
            warn(row, "оригинал в таблице не совпадает с игрой (строки перепутаны?)")
            skipped += 1
            continue
        new = join_head(head, ru)
        if sorted(sosxbb.TAG_RE.findall(en)) != sorted(sosxbb.TAG_RE.findall(ru)):
            warn(row, "теги в <угловых скобках> не совпадают с оригиналом, строка не взята")
            skipped += 1
            continue
        if widths is not None:
            missing = sorted({c for line in sosxbb.visible_lines(new) for c in line
                              if c not in widths})
            if missing:
                warn(row, "букв нет в шрифте игры: %s" % " ".join(missing))
            limit = max(args.max_width,
                        max(sum(widths.get(c, 0) for c in line)
                            for line in sosxbb.visible_lines(e["en"])))
            for line in sosxbb.visible_lines(new):
                w = sum(widths.get(c, 0) for c in line)
                if w > limit:
                    warn(row, "строка шире %d пикселей (%d): %s" % (limit, w, line))
            pages = max(args.max_lines, sosxbb.page_lines(e["en"]))
            if sosxbb.page_lines(new) > pages:
                warn(row, "больше %d строк на странице, добавьте <PAGE> или сократите"
                     % pages)
        if e.get(LANG) == new:
            same += 1
            continue
        e[LANG] = new
        changed.add(file_name)
        taken += 1

    if not args.dry_run:
        for name in changed:
            path, doc, _index = docs[name]
            sosxbb.write_json(path, doc)
    print("принято новых переводов: %d, без изменений: %d, пропущено: %d"
          % (taken, same, skipped))
    print("замечаний: %d%s" % (problems, " (показаны первые %d)" % args.limit
                               if problems > args.limit else ""))
    if args.dry_run:
        print("проверка без записи (--dry-run): файлы не изменены")
    else:
        print("обновлено файлов: %d" % len(changed))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Перевод текстов Story of Seasons: Trio of Towns в таблице")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="выгрузить тексты в таблицу .xlsx или .csv")
    p.add_argument("textdir", help="папка с JSON после sosxbb.py unpack (например text/Msg)")
    p.add_argument("table", help="файл таблицы: .xlsx или .csv")
    p.add_argument("--untranslated", action="store_true", help="только непереведённые строки")
    p.add_argument("--files", nargs="+", metavar="ШАБЛОН",
                   help='только эти файлы, можно со звёздочкой: "Talk_*"')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="загрузить переводы из таблицы в JSON")
    p.add_argument("table")
    p.add_argument("textdir")
    p.add_argument("--font", help="mainfont.bffnt: проверять ширину строк и буквы")
    p.add_argument("--max-width", type=int, default=280, help="ширина строки, пикселей")
    p.add_argument("--max-lines", type=int, default=3, help="строк на странице")
    p.add_argument("--limit", type=int, default=100, help="сколько замечаний показать")
    p.add_argument("--dry-run", action="store_true", help="только проверить, ничего не записывать")
    p.set_defaults(func=cmd_import)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (sosxbb.FormatError, OSError) as exc:
        print("ошибка: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
