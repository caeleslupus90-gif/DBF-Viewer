"""Ядро чтения/записи DBF. Без внешних зависимостей."""
from __future__ import annotations

import os
import re
import shutil
import struct
import csv

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator, Sequence

__all__ = ["DBF", "Field", "Record", "DBFError"]


class DBFError(Exception):
    pass


LANG = {
    0x01: "cp437", 0x02: "cp850", 0x03: "cp1252", 0x08: "cp865",
    0x0F: "cp866", 0x10: "cp866", 0x13: "cp932", 0x17: "cp866",
    0x1C: "cp863", 0x1F: "cp852", 0x22: "cp852", 0x23: "cp852",
    0x24: "cp866", 0x26: "cp866", 0x4D: "cp936", 0x4E: "cp949",
    0x4F: "cp950", 0x50: "cp874", 0x57: "cp1252", 0x65: "cp866",
    0x66: "cp865", 0x67: "cp866", 0x6A: "cp737", 0x6B: "cp857",
    0x86: "cp737", 0x87: "cp852", 0x88: "cp857",
    0xC8: "cp1250", 0xC9: "cp1251", 0xCA: "cp1252", 0xCB: "cp1253",
    0xCC: "cp1254", 0xCD: "cp1255", 0xCE: "cp1256", 0xCF: "cp1257",
}

MEMO_KINDS = {0x83: "dbt3", 0x8B: "dbt4", 0xF5: "fpt",
              0x30: "fpt", 0x31: "fpt", 0x32: "fpt"}

_JULIAN_1970 = 2440588
_ORD_1970 = date(1970, 1, 1).toordinal()


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    length: int
    decimals: int = 0
    offset: int = 0          # смещение внутри записи (с учётом флага удаления)


@dataclass
class Record:
    index: int               # 0-based
    deleted: bool
    data: dict[str, Any]


def _norm_fields(specs: Sequence) -> list[Field]:
    out, off = [], 1
    for s in specs:
        f = Field(*s) if isinstance(s, (tuple, list)) else s
        # FoxPro/VFP поддерживает до 11 символов, dBASE - до 10
        if not 1 <= len(f.name) <= 11:
            raise DBFError(f"Некорректное имя поля: {f.name!r} (длина {len(f.name)}, допустимо 1-11)")
        if f.type not in "CNFDLMBIYTG+QVP":
            raise DBFError(f"Неизвестный тип поля {f.type!r} ({f.name})")
        out.append(Field(f.name, f.type, f.length, f.decimals, off))
        off += f.length
    return out

_TOKEN_RE = re.compile(
    r"\b(?P<op>AND|OR)\b"
    r"|(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<cmp>!=|>=|<=|=|>|<)\s*"
    r"(?P<val>\"[^\"]*\"|'[^']*'|[^\s]+)", re.I)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _parse_filter(expr: str) -> list[tuple[str, str, str, str]]:
    """'A=1 AND B>2 OR C!="x"' -> [(logic, field, cmp, value), ...]"""
    tokens, pos = [], 0
    for m in _TOKEN_RE.finditer(expr):
        gap = expr[pos:m.start()].strip()
        if gap:
            raise DBFError(f"Не разобрано в фильтре: {gap!r}")
        pos = m.end()
        if m.group("op"):
            tokens.append((m.group("op").upper(), "", "", ""))
        else:
            tokens.append(("COND", m.group("field"), m.group("cmp"),
                           _unquote(m.group("val"))))
    if expr[pos:].strip():
        raise DBFError(f"Не разобрано в фильтре: {expr[pos:].strip()!r}")
    if not any(t[0] == "COND" for t in tokens):
        raise DBFError("Пустой или некорректный фильтр")
    return tokens


def _coerce(f: Field, token: str):
    t = token.strip()
    if t.upper() in ("NULL", "NONE", ".NULL."):
        return None
    if f.type == "L":
        return t.upper() in ("T", "TRUE", "Y", "YES", "1", ".T.")
    if f.type == "D":
        try:
            return date.fromisoformat(t)
        except ValueError:
            try:
                return date(int(t[:4]), int(t[4:6]), int(t[6:8]))
            except (ValueError, IndexError):
                raise DBFError(f"{f.name}: не дата {token!r}") from None
    if f.type in ("N", "F", "I", "Y", "B", "+"):
        try:
            return Decimal(t)
        except InvalidOperation:
            raise DBFError(f"{f.name}: не число {token!r}") from None
    return token


def _compare(val, op: str, target) -> bool:
    if val is None or target is None:
        eq = val is None and target is None
        return eq if op == "=" else (not eq if op == "!=" else False)
    if isinstance(target, Decimal):
        try:
            val = Decimal(str(val))
        except (InvalidOperation, ValueError):
            return op == "!="
    elif isinstance(target, date) and not isinstance(target, datetime):
        if isinstance(val, datetime):
            val = val.date()
    elif isinstance(target, str) and not isinstance(val, str):
        val = str(val)
    try:
        if op == "=":  return val == target
        if op == "!=": return val != target
        if op == ">":  return val > target
        if op == "<":  return val < target
        if op == ">=": return val >= target
        if op == "<=": return val <= target
    except TypeError:
        return False
    return False


def _eval_filter(tokens, rec: Record, db: "DBF") -> bool:
    """AND имеет приоритет над OR, скобок нет."""
    total, group, logic = False, None, "AND"
    for kind, a, b, c in tokens:
        if kind == "COND":
            f = db.field(a)
            cur = _compare(rec.data[f.name], b, _coerce(f, c))
            if group is None:
                group = cur
            elif logic == "AND":
                group = group and cur
            else:
                total = total or group
                group = cur
        else:
            logic = a
    return bool(total or group)


class _MemoFile:
    """FPT (FoxPro/VFP) и DBT (dBASE III)."""

    def __init__(self, dbf_path: str, kind: str, encoding: str, writable: bool):
        if kind == "dbt4":
            raise DBFError("dBASE IV .dbt пока не поддерживается")
        ext = ".fpt" if kind == "fpt" else ".dbt"
        base = os.path.splitext(dbf_path)[0]
        self.path = base + ext if os.path.exists(base + ext) else base + ext.upper()
        self.kind, self.encoding = kind, encoding
        self.fh = open(self.path, "r+b" if writable else "rb")
        self.block_size = 512
        self._read_header()

    def _read_header(self):
        self.fh.seek(0)
        h = self.fh.read(512)
        size = os.path.getsize(self.path)
        if self.kind == "fpt":
            self.next_block = struct.unpack_from(">I", h, 0)[0]
            self.block_size = struct.unpack_from(">H", h, 6)[0] or 512
        else:
            self.next_block = struct.unpack_from("<I", h, 0)[0]
        if self.next_block <= 0:
            self.next_block = max(1, size // self.block_size)

    def read(self, block: int):
        if not block:
            return None
        if self.kind == "fpt":
            self.fh.seek(block * self.block_size)
            hdr = self.fh.read(8)
            if len(hdr) < 8:
                return None
            btype, length = struct.unpack(">II", hdr)
            raw = self.fh.read(length)
            return raw.decode(self.encoding, "replace") if btype == 1 else raw
        self.fh.seek(block * 512)
        limit = max(self.next_block, block + 1) * 512
        buf = bytearray()
        while self.fh.tell() < limit:
            chunk = self.fh.read(512)
            if not chunk:
                break
            i = chunk.find(b"\x1a")
            if i >= 0:
                buf += chunk[:i]
                break
            buf += chunk
        return bytes(buf).rstrip(b"\x00").decode(self.encoding, "replace")

    def write(self, value) -> int:
        is_text = isinstance(value, str)
        data = value.encode(self.encoding) if is_text else bytes(value)
        block = self.next_block
        if self.kind == "fpt":
            bs = self.block_size
            payload = struct.pack(">II", 1 if is_text else 0, len(data)) + data
            used = (len(payload) + bs - 1) // bs
            self.fh.seek(block * bs)
            self.fh.write(payload.ljust(used * bs, b"\x00"))
            self.next_block = block + used
            self.fh.seek(0)
            self.fh.write(struct.pack(">I", self.next_block))
        else:
            payload = data + b"\x1a"
            used = (len(payload) + 511) // 512
            self.fh.seek(block * 512)
            self.fh.write(payload.ljust(used * 512, b"\x00"))
            self.next_block = block + used
            self.fh.seek(0)
            self.fh.write(struct.pack("<I", self.next_block))
        self.fh.flush()
        return block

    def close(self):
        self.fh.close()


class DBF:
    def __init__(self, path: str, mode: str = "r",
                 encoding: str | None = None, backup: bool = False):
        if mode not in ("r", "rw"):
            raise ValueError("mode: 'r' или 'rw'")
        self.path, self.mode = str(path), mode
        self._writable = mode == "rw"
        if self._writable and backup:
            shutil.copy2(self.path, self.path + ".bak")
        self._fh = open(self.path, "r+b" if self._writable else "rb")
        self._memo = None
        self._parse_header(encoding)

    # ---------- заголовок ----------
    def _parse_header(self, encoding):
        h = self._fh.read(32)
        if len(h) != 32:
            raise DBFError("Файл слишком мал или не является DBF")
        self.version = h[0]
        self.record_count = struct.unpack_from("<I", h, 4)[0]
        self.header_len, self.record_len = struct.unpack_from("<HH", h, 8)
        self.language_driver = h[29]
        self.memo_kind = MEMO_KINDS.get(self.version)
        try:
            yy = h[1] + (2000 if h[1] < 80 else 1900)
            self.updated = date(yy, h[2] or 1, h[3] or 1)
        except ValueError:
            self.updated = None
        self.encoding = encoding or LANG.get(self.language_driver, "cp866")

        self._fh.seek(32)
        raw_fields, off = [], 1
        while True:
            d = self._fh.read(32)
            if not d or d[0] == 0x0D:
                break
            if len(d) < 32:
                raise DBFError("Повреждённый дескриптор поля")
            name = d[:11].split(b"\x00")[0].decode(self.encoding, "replace").strip()
            raw_fields.append((name, chr(d[11]), d[16], d[17]))
            off += d[16]
        if not raw_fields:
            raise DBFError("Не найдено ни одного поля")
        self.fields: tuple[Field, ...] = tuple(_norm_fields(raw_fields))
        self._by_name = {f.name.upper(): f for f in self.fields}
        if off > self.record_len:
            raise DBFError("Сумма длин полей больше длины записи")

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> Field:
        f = self._by_name.get(name.upper())
        if f is None:
            raise DBFError(f"Нет поля {name!r}")
        return f

    @property
    def memo(self) -> _MemoFile:
        if self._memo is None:
            if not self.memo_kind:
                raise DBFError("У файла нет мемо-полей")
            self._memo = _MemoFile(self.path, self.memo_kind,
                                   self.encoding, self._writable)
        return self._memo

    # ---------- чтение ----------
    def _raw(self, index: int) -> bytes:
        if not 0 <= index < self.record_count:
            raise IndexError(f"Запись {index} вне диапазона 0..{self.record_count - 1}")
        self._fh.seek(self.header_len + index * self.record_len)
        raw = self._fh.read(self.record_len)
        if len(raw) != self.record_len:
            raise DBFError(f"Запись {index} обрезана")
        return raw

    def record(self, index: int, load_memo: bool = False) -> Record:
        raw = self._raw(index)
        data = {f.name: self._decode(f, raw[f.offset:f.offset + f.length], load_memo)
                for f in self.fields}
        return Record(index, raw[0:1] == b"*", data)

    def __getitem__(self, i: int) -> Record:
        return self.record(i)

    def __len__(self) -> int:
        return self.record_count

    def __iter__(self) -> Iterator[Record]:
        return self.records()

    def records(self, start: int = 0, count: int | None = None,
                include_deleted: bool = True, load_memo: bool = False) -> Iterator[Record]:
        end = self.record_count if count is None else min(self.record_count, start + count)
        for i in range(start, end):
            r = self.record(i, load_memo)
            if include_deleted or not r.deleted:
                yield r

    def memo_text(self, index: int, field_name: str):
        f = self.field(field_name)
        if f.type not in "MG":
            raise DBFError(f"Поле {f.name} не мемо")
        return self.memo.read(self.record(index).data[f.name] or 0)

    def _decode(self, f: Field, raw: bytes, load_memo: bool):
        t = f.type
        if t == "C":
            return raw.decode(self.encoding, "replace").rstrip(" \x00")
        if t in ("N", "F"):
            s = raw.strip()
            if not s or s == b"." or set(s) == {42}:
                return None
            try:
                txt = s.decode("ascii")
                return Decimal(txt) if (f.decimals or "." in txt or "E" in txt.upper()) else int(txt)
            except (InvalidOperation, ValueError, UnicodeDecodeError):
                return s.decode(self.encoding, "replace")
        if t == "D":
            s = raw.strip()
            if not s:
                return None
            try:
                return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            except (ValueError, IndexError):
                return s.decode(self.encoding, "replace")
        if t == "L":
            c = raw[:1].upper()
            return True if c in (b"T", b"Y") else False if c in (b"F", b"N") else None
        if t in ("I", "+"):
            return struct.unpack("<i", raw)[0] if len(raw) == 4 else None
        if t == "T":
            j, ms = struct.unpack("<ii", raw)
            if j == 0:
                return None
            return (datetime.fromordinal(_ORD_1970 + j - _JULIAN_1970)
                    + __import__("datetime").timedelta(milliseconds=ms))
        if t == "Y":
            return Decimal(struct.unpack("<q", raw)[0]) / 10000
        if t == "B" and len(raw) == 8 and self.version in (0x30, 0x31, 0x32, 0xF5):
            return struct.unpack("<d", raw)[0]
        if t in ("M", "G") or t == "B":
            if len(raw) == 4:
                block = struct.unpack("<I", raw)[0]
            else:
                s = raw.strip()
                block = int(s) if s.isdigit() else 0
            return self.memo.read(block) if (load_memo and block) else block
        return raw

    # ---------- запись ----------
    def _check_write(self):
        if not self._writable:
            raise DBFError("Файл открыт только для чтения (mode='rw')")

    def update(self, index: int, values: dict[str, Any]) -> int:
        self._check_write()
        buf = bytearray(self._raw(index))
        for name, val in values.items():
            f = self.field(name)
            buf[f.offset:f.offset + f.length] = self._pack(f, val)
        self._fh.seek(self.header_len + index * self.record_len)
        self._fh.write(bytes(buf))
        self._touch()
        return index

    def append(self, values: dict[str, Any]) -> int:
        self._check_write()
        buf = bytearray(b" " * self.record_len)
        for name, val in values.items():
            f = self.field(name)
            buf[f.offset:f.offset + f.length] = self._pack(f, val)
        index = self.record_count
        self._fh.seek(self.header_len + index * self.record_len)
        self._fh.write(bytes(buf) + b"\x1a")
        self.record_count += 1
        self._fh.seek(4)
        self._fh.write(struct.pack("<I", self.record_count))
        self._touch()
        return index

    def set_deleted(self, index: int, deleted: bool = True):
        self._check_write()
        self._raw(index)
        self._fh.seek(self.header_len + index * self.record_len)
        self._fh.write(b"*" if deleted else b" ")
        self._touch()

    # ---------- служебное для массовых операций ----------
    def _memo_path(self) -> str | None:
        if not self.memo_kind:
            return None
        base = os.path.splitext(self.path)[0]
        ext = ".fpt" if self.memo_kind == "fpt" else ".dbt"
        for e in (ext, ext.upper()):
            if os.path.exists(base + e):
                return base + e
        return base + ext

    def _backup(self):
        """Обязательный .bak для DBF и memo. Всегда перезаписывается."""
        if self._memo is not None:
            self._memo.close()
            self._memo = None
        self._fh.flush()
        shutil.copy2(self.path, self.path + ".bak")
        mp = self._memo_path()
        if mp and os.path.exists(mp):
            shutil.copy2(mp, mp + ".bak")

    @staticmethod
    def _empty_memo(path: str, kind: str, block_size: int = 512):
        hdr = bytearray(512)
        if kind == "fpt":
            struct.pack_into(">I", hdr, 0, 1)
            struct.pack_into(">H", hdr, 6, block_size)
        else:
            struct.pack_into("<I", hdr, 0, 1)
        with open(path, "wb") as m:
            m.write(hdr)

    @staticmethod
    def _memo_block(f: Field, raw: bytes) -> int:
        b = raw[f.offset:f.offset + f.length]
        if f.length == 4:
            return struct.unpack("<I", b)[0]
        s = b.strip()
        return int(s) if s.isdigit() else 0

    # ---------- ZAP / PACK ----------
    def zap(self):
        """Удалить все записи (ZAP). Обязательный .bak."""
        self._check_write()
        if self.record_count == 0:
            return
        kind, mp = self.memo_kind, self._memo_path()
        has_memo = bool(kind and mp and os.path.exists(mp))
        bs = self.memo.block_size if has_memo else 512
        self._backup()
        self.record_count = 0
        self._fh.seek(4)
        self._fh.write(struct.pack("<I", 0))
        self._fh.seek(self.header_len)
        self._fh.write(b"\x1a")
        self._fh.truncate()
        self._touch()
        if has_memo:
            self._empty_memo(mp, kind, bs)
            self._memo = _MemoFile(self.path, kind, self.encoding, True)

    def pack(self):
        """Убрать помеченные записи (PACK). Обязательный .bak.
        Вариант B: собираем живые записи + мемо в память, пишем memo с нуля."""
        self._check_write()
        if self.record_count == 0:
            return
        kind, mp = self.memo_kind, self._memo_path()
        has_memo = bool(kind and mp and os.path.exists(mp))
        bs = self.memo.block_size if has_memo else 512

        kept, memos = [], []
        for i in range(self.record_count):
            raw = self._raw(i)
            if raw[0:1] == b"*":
                continue
            vals = {}
            if has_memo:
                for f in self.fields:
                    if f.type in ("M", "G"):
                        blk = self._memo_block(f, raw)
                        if blk:
                            vals[f.name] = self.memo.read(blk)
            kept.append(bytearray(raw))
            memos.append(vals)

        self._backup()  # закрывает memo

        if has_memo:
            self._empty_memo(mp, kind, bs)
            self._memo = _MemoFile(self.path, kind, self.encoding, True)
            for buf, vals in zip(kept, memos):
                for name, val in vals.items():
                    f = self.field(name)
                    blk = self._memo.write(val)
                    buf[f.offset:f.offset + f.length] = (
                        struct.pack("<I", blk) if f.length == 4
                        else str(blk).rjust(f.length).encode("ascii"))

        self._fh.seek(self.header_len)
        for buf in kept:
            self._fh.write(bytes(buf))
        self._fh.write(b"\x1a")
        self._fh.truncate()
        self.record_count = len(kept)
        self._fh.seek(4)
        self._fh.write(struct.pack("<I", self.record_count))
        self._touch()
    
    def _make_predicate(self, filter_expr, filter_py):
        if filter_expr and filter_py:
            raise DBFError(f"filter-py: нужно выражение, а не инструкция ({e})") from None
        if filter_expr:
            tokens = _parse_filter(filter_expr)
            for kind, name, _, _ in tokens:
                if kind == "COND":
                    self.field(name)
            return lambda r: _eval_filter(tokens, r, self)
        if filter_py:
            try:
                code = compile(filter_py, "<filter-py>", "eval")
            except SyntaxError as e:
                raise DBFError(f"filter-py: нужно выражение, а не语句 ({e})") from None
            helpers = {"date": date, "datetime": datetime, "Decimal": Decimal,
                       "str": str, "int": int, "float": float, "bool": bool,
                       "len": len, "abs": abs, "round": round,
                       "min": min, "max": max, "sum": sum,
                       "any": any, "all": all, "sorted": sorted}

            def pred(r: Record) -> bool:
                env = dict(helpers)
                env.update(rec=r, data=r.data, index=r.index, deleted=r.deleted)
                env.update(r.data)
                try:
                    return bool(eval(code, {"__builtins__": {}}, env))
                except Exception as e:
                    raise DBFError(f"filter-py: {e}") from None
            return pred
        return None

    def copy_to(self, path: str, fields=None, filter_expr=None,
                filter_py=None, include_deleted: bool = False) -> int:
        """COPY TO: новый DBF с выбранными полями/записями. Возвращает число записей."""
        path = str(path)
        if os.path.abspath(path) == os.path.abspath(self.path):
            raise DBFError("Приёмник совпадает с источником")

        flds, seen = [], set()
        for n in (fields if fields else self.field_names):
            f = self.field(str(n).strip())
            if f.name.upper() not in seen:
                seen.add(f.name.upper())
                flds.append(f)
        if not flds:
            raise DBFError("Не выбрано ни одного поля")

        pred = self._make_predicate(filter_expr, filter_py)
        need_memo = any(f.type in ("M", "G") for f in flds)
        bs = 512
        if need_memo:
            mp = self._memo_path()
            if not mp or not os.path.exists(mp):
                raise DBFError("У источника нет мемо-файла — мемо-поля скопировать нельзя")
            bs = self.memo.block_size

        # обязательный .bak приёмника (если он уже существует)
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
            base = os.path.splitext(path)[0]
            for ext in (".fpt", ".dbt", ".FPT", ".DBT"):
                if os.path.exists(base + ext):
                    shutil.copy2(base + ext, base + ext + ".bak")

        special = any(f.type in "I+TYB" for f in flds)
        if need_memo:
            version = self.version if self.memo_kind else 0x83
        else:
            version = 0x30 if special else 0x03

        specs = [(f.name, f.type, f.length, f.decimals) for f in flds]
        out = DBF.create(path, specs, self.encoding, memo=False, version=version)
        try:
            if need_memo:
                kind = MEMO_KINDS.get(version, "dbt3")
                ext = ".fpt" if kind == "fpt" else ".dbt"
                self._empty_memo(os.path.splitext(path)[0] + ext, kind, bs)
            n = 0
            out._fh.seek(out.header_len)
            for r in self.records(include_deleted=include_deleted, load_memo=need_memo):
                if pred is not None and not pred(r):
                    continue
                buf = bytearray(b" " * out.record_len)
                for src, dst in zip(flds, out.fields):
                    buf[dst.offset:dst.offset + dst.length] = out._pack(dst, r.data[src.name])
                out._fh.write(bytes(buf))
                n += 1
            out._fh.write(b"\x1a")
            out.record_count = n
            out._fh.seek(4)
            out._fh.write(struct.pack("<I", n))
            out._touch()
            out.flush()
            return n
        finally:
            out.close()

    def append_from(self, source, is_csv, no_header=False, force=False, skip=False):
        """Импорт из CSV или DBF. Возвращает (added, skipped)."""
        self._check_write()
        
        if is_csv:
            return self._append_from_csv(source, no_header, force, skip)
        else:
            return self._append_from_dbf(source, force, skip)

    def _append_from_csv(self, path, no_header, force, skip):
        """Импорт из CSV."""
        added, skipped = 0, 0
        
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=";")
            
            # Определить маппинг полей
            if no_header:
                src_fields = self.field_names
                header = src_fields
                start_row = 1
            else:
                header = next(reader)
                src_fields = [h.strip() for h in header]
                start_row = 2
            
            # Маппинг: индекс в CSV → имя поля в приёмнике
            mapping = {}
            for i, src_name in enumerate(src_fields):
                try:
                    dst_field = self.field(src_name)
                    mapping[i] = dst_field.name
                except DBFError:
                    pass
            
            if not mapping:
                raise DBFError("Нет совпадающих полей между источником и приёмником")
            
            # Читать строки
            for row_num, row in enumerate(reader, start=start_row):
                try:
                    values = {}
                    for i, dst_name in mapping.items():
                        if i < len(row):
                            values[dst_name] = row[i]
                    
                    self.append(values)
                    added += 1
                except Exception as e:
                    if skip:
                        skipped += 1
                        continue
                    elif force:
                        try:
                            cleaned = {}
                            for k, v in values.items():
                                f = self.field(k)
                                if f.type in ("N", "F", "I", "+", "Y", "B"):
                                    try:
                                        cleaned[k] = Decimal(v.strip()) if v.strip() else None
                                    except:
                                        cleaned[k] = None
                                elif f.type == "D":
                                    try:
                                        cleaned[k] = date.fromisoformat(v.strip()) if v.strip() else None
                                    except:
                                        cleaned[k] = None
                                elif f.type == "L":
                                    cleaned[k] = v.strip().upper() in ("T", "TRUE", "Y", "YES", "1", ".T.") if v.strip() else None
                                else:
                                    cleaned[k] = v
                            self.append(cleaned)
                            added += 1
                        except Exception as e2:
                            if skip:
                                skipped += 1
                                continue
                            raise DBFError(f"Строка {row_num}: {e2}") from None
                    else:
                        raise DBFError(f"Строка {row_num}: {e}") from None
        
        return added, skipped

    def _append_from_dbf(self, path, force, skip):
        """Импорт из DBF."""
        added, skipped = 0, 0
        
        # Открываем источник без with, чтобы точно контролировать close
        src = DBF(path, "r", self.encoding)
        try:
            # Определить маппинг полей
            mapping = {}
            for src_field in src.fields:
                try:
                    dst_field = self.field(src_field.name)
                    mapping[src_field.name] = dst_field.name
                except DBFError:
                    pass
            
            if not mapping:
                raise DBFError("Нет совпадающих полей между источником и приёмником")
            
            # Мемо-конфликт: в источнике есть мемо, а приёмник его не хранит
            src_memo = [f.name for f in src.fields if f.type in ("M", "G")]
            if src_memo and not self.memo_kind:
                if not force:
                    raise DBFError(
                        f"В источнике есть мемо-поля ({', '.join(src_memo)}), "
                        f"а в приёмнике нет мемо-файла. "
                        f"Используйте --force, чтобы импортировать без них."
                    )
                for n in src_memo:
                    mapping.pop(n, None)
                if not mapping:
                    raise DBFError("После исключения мемо-полей нечего импортировать")

            load_memo = any(self.field(n).type in ("M", "G") for n in mapping.values())

            # Копировать записи
            for r in src.records(load_memo=load_memo):
                try:
                    values = {}
                    for src_name, dst_name in mapping.items():
                        values[dst_name] = r.data[src_name]
                    
                    self.append(values)
                    added += 1
                except Exception as e:
                    if skip:
                        skipped += 1
                        continue
                    else:
                        raise DBFError(f"Запись {r.index}: {e}") from None
        finally:
            src.close()
        
        return added, skipped

    def _touch(self):
        t = date.today()
        self._fh.seek(1)
        self._fh.write(bytes([t.year % 100, t.month, t.day]))
        self._fh.flush()

    def _blank(self, f: Field) -> bytes:
        if f.type in ("I", "+", "T", "Y") or (f.type == "M" and f.length == 4):
            return b"\x00" * f.length
        if f.type == "L":
            return b"?"
        return b" " * f.length

    def _pack(self, f: Field, v) -> bytes:
        if v is None or v == "":
            return self._blank(f)
        t = f.type
        if t == "C":
            b = str(v).encode(self.encoding)
            if len(b) > f.length:
                raise DBFError(f"{f.name}: {len(b)} байт > {f.length}")
            return b.ljust(f.length)
        if t in ("N", "F"):
            d = Decimal(str(v))
            s = f"{d:.{f.decimals}f}" if f.decimals else (
                str(int(d)) if d == d.to_integral_value() else f"{d:f}")
            if len(s) > f.length:
                raise DBFError(f"{f.name}: значение {s!r} не влезает в {f.length}")
            return s.rjust(f.length).encode("ascii")
        if t == "D":
            if isinstance(v, str):
                v = date.fromisoformat(v) if "-" in v else date(
                    int(v[:4]), int(v[4:6]), int(v[6:8]))
            if isinstance(v, datetime):
                v = v.date()
            return v.strftime("%Y%m%d").encode("ascii")
        if t == "L":
            if isinstance(v, str):
                return (v.strip()[:1].upper() or "?").encode("ascii")
            return b"T" if v else b"F"
        if t in ("I", "+"):
            return struct.pack("<i", int(v))
        if t == "Y":
            return struct.pack("<q", int(Decimal(str(v)) * 10000))
        if t == "B" and f.length == 8:
            return struct.pack("<d", float(v))
        if t == "T":
            if isinstance(v, date) and not isinstance(v, datetime):
                v = datetime(v.year, v.month, v.day)
            j = v.date().toordinal() - _ORD_1970 + _JULIAN_1970
            ms = (v.hour * 3600 + v.minute * 60 + v.second) * 1000 + v.microsecond // 1000
            return struct.pack("<ii", j, ms)
        if t in ("M", "G"):
            block = self.memo.write(v) if isinstance(v, (str, bytes)) else int(v)
            return struct.pack("<I", block) if f.length == 4 \
                else str(block).rjust(f.length).encode("ascii")
        if isinstance(v, bytes) and len(v) == f.length:
            return v
        raise DBFError(f"{f.name}: тип {t} не поддерживается для записи")

    # ---------- создание ----------
    @classmethod
    def create(cls, path: str, fields: Sequence, encoding: str = "cp866",
               memo: bool = False, version: int | None = None) -> "DBF":
        flds = _norm_fields(fields)
        version = version or (0x83 if memo else 0x03)
        header_len = 32 + 32 * len(flds) + 1
        record_len = 1 + sum(f.length for f in flds)
        lang = next((k for k, v in LANG.items() if v == encoding.lower()), 0x00)
        t = date.today()
        # FoxPro версии поддерживают 11 символов, остальные - 10
        max_name_len = 11 if version in (0x30, 0x31, 0x32, 0xF5, 0x83, 0x8B) else 10
        with open(path, "wb") as fh:
            fh.write(struct.pack("<BBBBIHH", version, t.year % 100, t.month, t.day,
                                 0, header_len, record_len))
            fh.write(b"\x00" * 16 + bytes([0, lang, 0, 0]))
            for f in flds:
                name = f.name.encode(encoding)[:max_name_len].ljust(11, b"\x00")
                fh.write(name + f.type.encode("ascii") + b"\x00" * 4
                         + bytes([f.length, f.decimals]) + b"\x00" * 14)
            fh.write(b"\x0d\x1a")
        if memo:
            with open(os.path.splitext(path)[0] + ".dbt", "wb") as m:
                m.write(struct.pack("<I", 1).ljust(512, b"\x00"))
        return cls(path, "rw", encoding)

    # ---------- служебное ----------
    def flush(self):
        self._fh.flush()
        if self._memo:
            self._memo.fh.flush()

    def close(self):
        try:
            self._fh.close()
        finally:
            if self._memo:
                self._memo.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()