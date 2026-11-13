"""GUI-просмотрщик DBF на tkinter."""
import csv
import os
import time
from datetime import date, datetime
from decimal import Decimal

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from dbf_core import DBF, DBFError

MIN_COL_WIDTH = 80
MAX_COL_WIDTH = 400
SAMPLE_LIMIT = 5000

FIELD_TYPES = {
    "C": "Символьный",
    "N": "Числовой",
    "F": "Число с плавающей точкой",
    "D": "Дата",
    "L": "Логический",
    "M": "Мемо",
    "G": "General",
    "I": "Целое число",
    "Y": "Денежный",
    "T": "ДатаВремя",
    "B": "Двойной точности",
    "V": "Переменная длина",
    "P": "Изображение",
    "+": "Автоинкремент",
    "Q": "Varbinary",
    "O": "Double",
}

class DBFViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("DBF Viewer")
        self.root.geometry("1200x700")
        
        self.db = None
        self.current_file = None
        self.filtered_records = []
        self._font = tkfont.nametofont("TkDefaultFont")
        
        # Кэш для мгновенной работы
        self._cache = []
        self._rows = {}
        self._searchable = {}
        self._search_timer = None
        
        self._create_menu()
        self._create_widgets()
        
    def _create_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Открыть", command=self.open_file, accelerator="Ctrl+O")
        file_menu.add_command(label="Сохранить", command=self.save_file, accelerator="Ctrl+S")
        file_menu.add_command(label="Экспорт в CSV", command=self.export_csv, accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.root.quit)
        menubar.add_cascade(label="Файл", menu=file_menu)
        
        record_menu = tk.Menu(menubar, tearoff=0)
        record_menu.add_command(label="Добавить запись", command=self.add_record)
        record_menu.add_command(label="Пометить на удаление", command=self.delete_record)
        record_menu.add_command(label="Восстановить запись", command=self.undelete_record)
        menubar.add_cascade(label="Запись", menu=record_menu)
        
        ops_menu = tk.Menu(menubar, tearoff=0)
        ops_menu.add_command(label="Очистить файл (ZAP)", command=self.zap_file)
        ops_menu.add_command(label="Сжать файл (PACK)", command=self.pack_file)
        ops_menu.add_command(label="Копировать в файл...", command=self.copy_to_file)
        ops_menu.add_command(label="Добавить из файла...", command=self.append_from_file)
        menubar.add_cascade(label="Операции", menu=ops_menu)
        
        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda e: self.open_file())
        self.root.bind("<Control-s>", lambda e: self.save_file())
        self.root.bind("<Control-e>", lambda e: self.export_csv())
    
    def _on_header_right_click(self, event):
        """Правый клик по заголовку столбца — показать свойства."""
        region = self.tree.identify("region", event.x, event.y)
        if region != "heading":
            return
        
        column = self.tree.identify_column(event.x)
        col_idx = int(column[1:]) - 1  # индекс в tree["columns"]
        
        # Индексы 0 и 1 — это "#" и "D", они не являются полями БД
        if col_idx < 2:
            return
        
        field_name = self.db.field_names[col_idx - 2]
        self._show_column_properties(field_name)


    def _show_column_properties(self, column_name):
        """Диалог со свойствами и статистикой столбца."""
        if not self.db:
            return
        
        field = self.db.field(column_name)
        
        # Собираем статистику по кэшу
        total = len(self._cache)
        filled = 0
        empty = 0
        deleted_count = 0
        unique_values = set()
        min_val = None
        max_val = None
        
        for r in self._cache:
            if r.deleted:
                deleted_count += 1
                continue
            
            val = r.data[column_name]
            if val is None or val == "" or (isinstance(val, str) and val.strip() == ""):
                empty += 1
            else:
                filled += 1
                unique_values.add(str(val))
                
                # Мин/макс для числовых и дат
                if isinstance(val, (int, float, Decimal)):
                    if min_val is None or val < min_val:
                        min_val = val
                    if max_val is None or val > max_val:
                        max_val = val
                elif isinstance(val, date):
                    if min_val is None or val < min_val:
                        min_val = val
                    if max_val is None or val > max_val:
                        max_val = val
        
        # Диалог
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Свойства столбца: {column_name}")
        dialog.geometry("450x450")
        dialog.transient(self.root)
        dialog.grab_set()
        
        ttk.Label(dialog, text=f"Столбец: {column_name}", 
                  font=("", 12, "bold")).pack(pady=10)
        
        # Фрейм для информации о поле
        info_frame = ttk.LabelFrame(dialog, text="Описание поля")
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Получаем расшифровку типа
        type_desc = FIELD_TYPES.get(field.type, f"Неизвестный ({field.type})")
        
        field_info = [
            f"Наименование: {field.name}",
            f"Тип: {field.type} ({type_desc})",
            f"Длина: {field.length}",
            f"Точность (decimals): {field.decimals}",
            f"Смещение в записи: {field.offset}",
        ]
        for line in field_info:
            ttk.Label(info_frame, text=line).pack(anchor="w", padx=10, pady=1)
        
        # Фрейм для статистики
        stats_frame = ttk.LabelFrame(dialog, text="Статистика")
        stats_frame.pack(fill=tk.X, padx=10, pady=5)
        
        stats = [
            f"Всего записей в файле: {total}",
            f"Заполнено значений: {filled}",
            f"Пустых значений: {empty}",
            f"Уникальных значений: {len(unique_values)}",
            f"Удалённых записей: {deleted_count}",
        ]
        if min_val is not None:
            stats.append(f"Минимум: {min_val}")
            stats.append(f"Максимум: {max_val}")
        
        for line in stats:
            ttk.Label(stats_frame, text=line).pack(anchor="w", padx=10, pady=1)
        
        # Кнопки
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(btn_frame, text="Закрыть", command=dialog.destroy).pack(side="right")
        
        dialog.bind("<Escape>", lambda e: dialog.destroy())
    
    def _create_widgets(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(toolbar, text="Открыть", command=self.open_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Сохранить", command=self.save_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Экспорт CSV", command=self.export_csv).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(toolbar, text="Добавить", command=self.add_record).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Пометить", command=self.delete_record).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Восстановить", command=self.undelete_record).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(toolbar, text="Очистить", command=self.zap_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Сжать", command=self.pack_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Копировать", command=self.copy_to_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Добавить из", command=self.append_from_file).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        
        self.chk_deleted = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="Показать удалённые", variable=self.chk_deleted,
                        command=self.refresh_table).pack(side=tk.LEFT, padx=2)
        
        # Поиск
        search_frame = ttk.Frame(self.root)
        search_frame.pack(fill=tk.X, padx=5, pady=2)
        
        ttk.Label(search_frame, text="Поиск:").pack(side=tk.LEFT, padx=2)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_change)
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side=tk.LEFT, padx=2)
        
        # Ограничение длины поиска (максимум 100 символов)
        vcmd = (self.root.register(self._validate_search), '%P')
        self.search_entry.configure(validate="key", validatecommand=vcmd)
        
        # Явные биндинги для копирования/вставки (работают на любой раскладке)
        self.search_entry.bind("<Control-c>", lambda e: (self._copy(), "break")[1])
        self.search_entry.bind("<Control-v>", lambda e: (self._paste(), "break")[1])
        self.search_entry.bind("<Control-x>", lambda e: (self._cut(), "break")[1])
        self.search_entry.bind("<Control-a>", lambda e: (self._select_all(), "break")[1])
        
        ttk.Label(search_frame, text="в поле:").pack(side=tk.LEFT, padx=(10, 2))
        self.search_field_var = tk.StringVar(value="Все поля")
        self.search_field_combo = ttk.Combobox(search_frame, textvariable=self.search_field_var,
                                               state="readonly", width=20)
        self.search_field_combo.pack(side=tk.LEFT, padx=2)
        
        table_frame = ttk.Frame(self.root)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.tree = ttk.Treeview(table_frame, show="headings", selectmode="extended")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Button-3>", self._on_header_right_click)
        
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        self.status = ttk.Label(self.root, text="Файл не открыт", anchor="w")
        self.status.pack(fill=tk.X, padx=5, pady=2)
        
        self.tree.bind("<Double-1>", self.on_double_click)


    def _validate_search(self, new_value):
        """Ограничиваем длину поиска 100 символами."""
        if len(new_value) > 100:
            messagebox.showwarning("Внимание", "Слишком длинный поисковый запрос (максимум 100 символов)")
            return False
        return True
    
    def _copy(self):
        try:
            text = self.search_entry.selection_get()
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass  # ничего не выделено
    
    def _paste(self):
        try:
            text = self.root.clipboard_get()
            if len(text) > 100:
                text = text[:100]
                messagebox.showwarning("Внимание", "Вставляемая строка обрезана до 100 символов")
            # Удаляем выделение, если оно есть
            try:
                self.search_entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                pass  # выделения нет — просто вставляем в позицию курсора
            self.search_entry.insert(tk.INSERT, text)
        except tk.TclError:
            pass
        return "break"
    
    def _cut(self):
        self._copy()
        try:
            self.search_entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass
    
    def _select_all(self):
        self.search_entry.selection_range(0, tk.END)
        self.search_entry.icursor(tk.END)

    def _on_search_change(self, *args):
        """Debounce: ждём 300 мс тишины перед обновлением."""
        if self._search_timer:
            self.root.after_cancel(self._search_timer)
        self._search_timer = self.root.after(300, self.refresh_table)
    
    def open_file(self):
        if self.db:
            self.db.close()
        
        path = filedialog.askopenfilename(
            title="Открыть DBF файл",
            filetypes=[("DBF файлы", "*.dbf"), ("Все файлы", "*.*")]
        )
        if not path:
            return
        
        try:
            print(f"\n[OPEN] Открываю {path}...")
            t0 = time.perf_counter()
            self.db = DBF(path, "rw", backup=True)
            self.current_file = path
            
            # Сброс кэша и колонок
            self._cache = []
            self._rows = {}
            self._searchable = {}
            self.tree["columns"] = ()
            
            self.search_field_combo["values"] = ["Все поля"] + self.db.field_names
            self.search_field_var.set("Все поля")
            
            self.refresh_table()
            print(f"[OPEN] Готово за {time.perf_counter() - t0:.3f}с")
        except (DBFError, OSError) as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{e}")
    
    def save_file(self):
        if not self.db:
            return
        try:
            self.db.flush()
            messagebox.showinfo("Успех", "Изменения сохранены")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")
    
    def export_csv(self):
        if not self.db:
            return
        path = filedialog.asksaveasfilename(title="Экспорт в CSV", defaultextension=".csv",
                                            filetypes=[("CSV файлы", "*.csv"), ("Все файлы", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["_DELETED"] + self.db.field_names)
                for r in self.db.records(include_deleted=self.chk_deleted.get(), load_memo=True):
                    row = [int(r.deleted)] + [self._format_value(r.data[f.name]) for f in self.db.fields]
                    writer.writerow(row)
            messagebox.showinfo("Успех", f"Экспортировано в {path}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось экспортировать:\n{e}")
    
    def _measure_text(self, text: str) -> int:
        return self._font.measure(str(text) if text else "") + 16
    
    def _calculate_column_widths(self) -> dict:
        max_widths = {}
        for f in self.db.fields:
            max_widths[f.name] = self._measure_text(f"{f.name} ({f.type})")
        
        sample_count = 0
        for r in self.db.records(include_deleted=True, load_memo=False):
            if sample_count >= SAMPLE_LIMIT:
                break
            for f in self.db.fields:
                w = self._measure_text(self._format_value(r.data[f.name]))
                if w > max_widths[f.name]:
                    max_widths[f.name] = w
            sample_count += 1
        
        return {name: max(MIN_COL_WIDTH, min(MAX_COL_WIDTH, w)) for name, w in max_widths.items()}
    
    def refresh_table(self):
        if not self.db:
            return
        t0 = time.perf_counter()
        self.tree.delete(*self.tree.get_children())

        # 1. Инициализация колонок (только один раз)
        if not self.tree["columns"]:
            t1 = time.perf_counter()
            self.tree["columns"] = ["#", "D"] + self.db.field_names
            self.tree.heading("#", text="#")
            self.tree.heading("D", text="D")
            self.tree.column("#", width=50, anchor="center", stretch=False)
            self.tree.column("D", width=30, anchor="center", stretch=False)
            widths = self._calculate_column_widths()
            for f in self.db.fields:
                self.tree.heading(f.name, text=f"{f.name} ({f.type})")
                self.tree.column(f.name, width=widths[f.name], anchor="w", stretch=False)
            print(f"  [UI] Колонки созданы за {time.perf_counter() - t1:.3f}с")
            t0 = time.perf_counter()

        # 2. Построение кэша (только один раз)
        if not self._cache:
            t1 = time.perf_counter()
            self._cache = list(self.db.records(include_deleted=True, load_memo=False))
            for r in self._cache:
                vals = [self._format_value(r.data[f.name]) for f in self.db.fields]
                self._rows[r.index] = vals
                self._searchable[r.index] = [str(v).lower() for v in vals]
            print(f"  [DATA] Кэш построен за {time.perf_counter() - t1:.3f}с")
            t0 = time.perf_counter()

        # 3. Фильтрация и отрисовка
        t1 = time.perf_counter()
        search_text = self.search_var.get().lower()
        search_field = self.search_field_var.get()
        include_deleted = self.chk_deleted.get()
        field_idx = None
        if search_text and search_field != "Все поля":
            field_idx = self.db.field_names.index(search_field)

        self.filtered_records = []
        rows_to_insert = []
        for r in self._cache:
            if not include_deleted and r.deleted:
                continue
            # --- ПОИСК ---
            if search_text:
                hay = self._searchable[r.index]
                if field_idx is None:
                    if not any(search_text in v for v in hay):
                        continue
                elif search_text not in hay[field_idx]:
                    continue
            # --- /ПОИСК ---
            self.filtered_records.append(r.index)
            mark = "*" if r.deleted else " "
            rows_to_insert.append((r.index, mark, r.deleted, self._rows[r.index]))

        for idx, mark, deleted, vals in rows_to_insert:
            tags = []
            if deleted:
                tags.append("deleted")
            if search_text:
                # Проверяем, есть ли совпадение в этой строке
                has_match = False
                if field_idx is None:
                    has_match = any(search_text in v for v in vals)
                else:
                    has_match = search_text in vals[field_idx]
                if has_match:
                    tags.append("highlight")
            self.tree.insert("", "end", values=[idx, mark] + vals, tags=tags)

        self.tree.tag_configure("deleted", foreground="red", background="#ffe0e0")
        self.status.config(text=f"{self.current_file} | Показано: {len(self.filtered_records)} из {len(self.db)} | Кодировка: {self.db.encoding}")
        print(f"  [SEARCH] Фильтрация и отрисовка за {time.perf_counter() - t1:.3f}с (всего {time.perf_counter() - t0:.3f}с)")


    def _format_value(self, v):
        if v is None:
            return ""
        if isinstance(v, bytes):
            return f"<binary {len(v)} bytes>"
        if isinstance(v, str) and len(v) > 100:
            return v[:100].replace("\n", " ") + "…"
        return str(v).replace("\n", "⏎")


    def on_double_click(self, event):
        if not self.db:
            return
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item:
            return
        col_idx = int(column[1:]) - 1
        if col_idx < 2:  # "#" или "D"
            return
        field_name = self.db.field_names[col_idx - 2]  # было col_idx - 1
        field = self.db.field(field_name)
        record_index = int(self.tree.set(item, "#"))
        if field.type in ("M", "G"):
            full_value = self.db.memo_text(record_index, field_name) or ""
            self._open_memo_editor(record_index, field_name, full_value)
        else:
            current_value = self.tree.set(item, field_name)
            self._open_simple_editor(item, record_index, field_name, current_value)


    def _open_simple_editor(self, item, record_index, field_name, current_value):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Редактирование: {field_name}")
        dialog.geometry("400x150")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text=f"Поле: {field_name}").pack(pady=5)
        entry = ttk.Entry(dialog, width=50)
        entry.insert(0, current_value)
        entry.pack(pady=5, padx=10, fill=tk.X)
        entry.select_range(0, tk.END)
        entry.focus()
        
        def save():
            try:
                self.db.update(record_index, {field_name: entry.get()})
                # Обновляем кэш
                r = self.db.record(record_index, load_memo=False)
                self._rows[r.index] = [self._format_value(r.data[f.name]) for f in self.db.fields]
                self._searchable[r.index] = [str(v).lower() for v in self._rows[r.index]]
                self.refresh_table()  # перерисует всю таблицу с новыми данными
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось обновить:\n{e}", parent=dialog)
        
        ttk.Button(dialog, text="Сохранить", command=save).pack(pady=5)
        dialog.bind("<Return>", lambda e: save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
    
    def _open_memo_editor(self, record_index, field_name, current_value):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Мемо-поле: {field_name}")
        dialog.geometry("600x400")
        dialog.transient(self.root)
        ttk.Label(dialog, text=f"Поле: {field_name} (запись #{record_index})").pack(pady=5)
        text = tk.Text(dialog, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        text.insert("1.0", current_value)
        
        def save():
            try:
                self.db.update(record_index, {field_name: text.get("1.0", tk.END).rstrip("\n")})
                self.refresh_table()
                dialog.destroy()
                messagebox.showinfo("Успех", "Мемо-поле обновлено")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось обновить:\n{e}", parent=dialog)
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(btn_frame, text="Сохранить", command=save).pack(side="right", padx=2)
        ttk.Button(btn_frame, text="Закрыть", command=dialog.destroy).pack(side="right", padx=2)
    
    def add_record(self):
        if not self.db:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Добавить запись")
        dialog.geometry("400x500")
        dialog.transient(self.root)
        dialog.grab_set()
        
        entries = {}
        canvas = tk.Canvas(dialog)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        for f in self.db.fields:
            ttk.Label(scroll_frame, text=f"{f.name} ({f.type}):").pack(anchor="w", padx=5, pady=2)
            entry = ttk.Entry(scroll_frame, width=50)
            entry.pack(padx=5, pady=2, fill=tk.X)
            entries[f.name] = entry
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        def save():
            try:
                idx = self.db.append({name: entry.get() for name, entry in entries.items()})
                self.refresh_table()
                dialog.destroy()
                messagebox.showinfo("Успех", f"Добавлена запись #{idx}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось добавить:\n{e}", parent=dialog)
        
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="Сохранить", command=save).pack(side="right", padx=2)
        ttk.Button(btn_frame, text="Отмена", command=dialog.destroy).pack(side="right", padx=2)
    
    def delete_record(self):
        if not self.db:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите записи")
            return
        indices = [int(self.tree.set(item, "#")) for item in selected]
        if messagebox.askyesno("Подтверждение",
                f"Пометить на удаление {len(indices)} записей?\n"
                f"Номера: {', '.join(str(i) for i in indices[:10])}"
                f"{'...' if len(indices) > 10 else ''}"):
            try:
                for i in indices:
                    self.db.set_deleted(i, True)
                    # Обновить кэш
                    for r in self._cache:
                        if r.index == i:
                            r.deleted = True
                            break
                self.refresh_table()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось удалить:\n{e}")

    def undelete_record(self):
        if not self.db:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Внимание", "Выберите записи")
            return
        indices = [int(self.tree.set(item, "#")) for item in selected]
        try:
            for i in indices:
                self.db.set_deleted(i, False)
                # Обновить кэш
                for r in self._cache:
                    if r.index == i:
                        r.deleted = False
                        break
            self.refresh_table()
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось восстановить:\n{e}")
    
    def zap_file(self):
        if not self.db:
            return
        if not messagebox.askyesno("ZAP",
                f"Удалить ВСЕ {len(self.db)} записей из {os.path.basename(self.current_file)}?\n"
                "Будет создан .bak"):
            return
        try:
            self.db.zap()
            self._reset_cache()
            self.refresh_table()
            messagebox.showinfo("ZAP", "Файл очищен")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
    
    def pack_file(self):
        if not self.db:
            return
        before = len(self.db)
        if not messagebox.askyesno("PACK",
                f"Физически удалить помеченные записи?\nЗаписей сейчас: {before}. Будет создан .bak"):
            return
        try:
            self.db.pack()
            self._reset_cache()
            self.refresh_table()
            messagebox.showinfo("PACK", f"Удалено: {before - len(self.db)}. Осталось: {len(self.db)}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
    
    def copy_to_file(self):
        if not self.db:
            return
        path = filedialog.asksaveasfilename(
            title="COPY TO", defaultextension=".dbf",
            filetypes=[("DBF", "*.dbf"), ("Все файлы", "*.*")])
        if not path:
            return
        # Простой диалог фильтра (можно расширить)
        filt = simpledialog.askstring("Фильтр (опционально)",
            "Выражение вроде: SUM>100 AND NAME='Иванов'\nОставь пустым, чтобы копировать всё:",
            parent=self.root)
        try:
            n = self.db.copy_to(path, filter_expr=filt or None,
                                include_deleted=self.chk_deleted.get())
            messagebox.showinfo("COPY TO", f"Скопировано {n} записей в {path}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
    
    def append_from_file(self):
        if not self.db:
            return
        path = filedialog.askopenfilename(
            title="APPEND FROM",
            filetypes=[("DBF/CSV", "*.dbf *.csv"), ("Все файлы", "*.*")])
        if not path:
            return
        is_csv = path.lower().endswith(".csv")
        no_header = False
        force = skip = False
        if is_csv:
            # Мини-диалог с чекбоксами
            dlg = tk.Toplevel(self.root); dlg.title("Параметры CSV"); dlg.grab_set()
            no_header_v = tk.BooleanVar(); force_v = tk.BooleanVar(); skip_v = tk.BooleanVar()
            ttk.Checkbutton(dlg, text="Нет заголовка", variable=no_header_v).pack(anchor="w", padx=10, pady=2)
            ttk.Checkbutton(dlg, text="force (принудительно преобразовывать)", variable=force_v).pack(anchor="w", padx=10, pady=2)
            ttk.Checkbutton(dlg, text="skip (пропускать битые строки)", variable=skip_v).pack(anchor="w", padx=10, pady=2)
            res = {"ok": False}
            def go(): res["ok"] = True; dlg.destroy()
            ttk.Button(dlg, text="OK", command=go).pack(pady=5)
            self.root.wait_window(dlg)
            if not res["ok"]: return
            no_header, force, skip = no_header_v.get(), force_v.get(), skip_v.get()
        try:
            added, skipped = self.db.append_from(path, is_csv, no_header, force, skip)
            self._reset_cache()
            self.refresh_table()
            messagebox.showinfo("APPEND", f"Добавлено: {added}, пропущено: {skipped}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
    
    def _reset_cache(self):
        self._cache = []
        self._rows = {}
        self._searchable = {}
        self.tree["columns"] = ()
        self.search_field_combo["values"] = ["Все поля"] + (self.db.field_names if self.db else [])

if __name__ == "__main__":
    root = tk.Tk()
    app = DBFViewer(root)
    root.mainloop()