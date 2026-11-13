"""Консольный просмотрщик/редактор DBF."""
import argparse
import csv
import sys

from dbf_core import DBF, DBFError


def assignments(items):
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"Ожидалось ПОЛЕ=значение, получено: {it}")
        k, v = it.split("=", 1)
        out[k] = v if v != "" else None
    return out


def table(rows, headers, maxw=40):
    cells = [[("" if v is None else str(v).replace("\n", "⏎"))[:maxw] for v in r] for r in rows]
    w = [len(h) for h in headers]
    for r in cells:
        w = [max(a, len(b)) for a, b in zip(w, r)]
    line = "-+-".join("-" * x for x in w)
    print(" | ".join(h.ljust(x) for h, x in zip(headers, w)))
    print(line)
    for r in cells:
        print(" | ".join(c.ljust(x) for c, x in zip(r, w)))


def main():
    p = argparse.ArgumentParser(description="Просмотр и правка DBF")
    p.add_argument("command", choices=[ "info",  "show",  "get",  "set",  "add",  "delete",  "undelete",  "memo",  "export",  "zap",  "pack",  "copy",  "append"])
    p.add_argument("file")
    p.add_argument("args", nargs="*")
    p.add_argument("-e", "--encoding")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("-o", "--offset", type=int, default=0)
    p.add_argument("-f", "--fields")
    p.add_argument("--hide-deleted", action="store_true")
    p.add_argument("--backup", action="store_true")
    p.add_argument("--filter", help='простой фильтр: "SUM>100 AND NAME=Иванов"')
    p.add_argument("--filter-py", help='Python-выражение: "SUM > 100 and NAME.startswith(\'И\')"')
    p.add_argument("--with-deleted", action="store_true", help="копировать и помеченные записи")
    p.add_argument("--no-header", action="store_true", help="append: в CSV нет заголовка")
    p.add_argument("--force", action="store_true", help="append: пытаться преобразовывать значения")
    p.add_argument("--skip", action="store_true", help="append: пропускать проблемные строки")
    a = p.parse_args()

    write = a.command in ("set", "add", "delete", "undelete", "zap", "pack", "append")
    try:
        with DBF(a.file, "rw" if write else "r", a.encoding, a.backup) as db:
            names = a.fields.split(",") if a.fields else db.field_names

            if a.command == "info":
                print(f"Файл:      {db.path}")
                print(f"Версия:    0x{db.version:02X}  мемо: {db.memo_kind or 'нет'}")
                print(f"Кодировка: {db.encoding} (driver 0x{db.language_driver:02X})")
                print(f"Записей:   {db.record_count}, длина {db.record_len}, "
                      f"заголовок {db.header_len}, изменён {db.updated}")
                table([(f.name, f.type, f.length, f.decimals, f.offset) for f in db.fields],
                      ["Поле", "Тип", "Длина", "Точн", "Смещ"])

            elif a.command == "show":
                recs = list(db.records(a.offset, a.limit, not a.hide_deleted))
                rows = [(r.index, "*" if r.deleted else " ",
                         *(r.data[n] for n in names)) for r in recs]
                table(rows, ["#", "D", *names])
                print(f"\nПоказано {len(recs)} из {db.record_count}")

            elif a.command == "get":
                r = db.record(int(a.args[0]), load_memo=True)
                print(f"# {r.index}{'  [удалена]' if r.deleted else ''}")
                for k, v in r.data.items():
                    print(f"{k:>12}: {v!r}")

            elif a.command == "set":
                i = int(a.args[0])
                db.update(i, assignments(a.args[1:]))
                print(f"Запись {i} обновлена")

            elif a.command == "add":
                print(f"Добавлена запись {db.append(assignments(a.args))}")
            elif a.command == "append":
                if not a.args:
                    raise SystemExit("append: укажите файл-источник")
                src = a.args[0]
                is_csv = src.lower().endswith(".csv")
                added, skipped = db.append_from(src, is_csv, a.no_header, a.force, a.skip)
                print(f"APPEND FROM: добавлено {added}, пропущено {skipped}")

            elif a.command in ("delete", "undelete"):
                for s in a.args:
                    db.set_deleted(int(s), a.command == "delete")
                print("Готово")

            elif a.command == "memo":
                print(db.memo_text(int(a.args[0]), a.args[1]))

            elif a.command == "export":
                out = a.args[0] if a.args else "out.csv"
                with open(out, "w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh, delimiter=";")
                    w.writerow(["_DELETED", *db.field_names])
                    for r in db.records(include_deleted=not a.hide_deleted, load_memo=True):
                        w.writerow([int(r.deleted), *r.data.values()])
                print(f"Экспортировано {db.record_count} записей -> {out}")
            
            elif a.command == "zap":
                count_before = db.record_count
                db.zap()
                print(f"ZAP: удалено {count_before} записей. Бэкап: {db.path}.bak")
            
            elif a.command == "pack":
                count_before = db.record_count
                db.pack()
                count_after = db.record_count
                removed = count_before - count_after
                print(f"PACK: удалено {removed} записей. Осталось {count_after}. Бэкап: {db.path}.bak")
            
            elif a.command == "copy":
                if not a.args:
                    raise SystemExit("copy: укажите файл-приёмник")
                n = db.copy_to(a.args[0], names if a.fields else None,
                               a.filter, a.filter_py, a.with_deleted)
                print(f"COPY TO: скопировано {n} записей -> {a.args[0]}")
            
    except (DBFError, IndexError, ValueError, OSError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()