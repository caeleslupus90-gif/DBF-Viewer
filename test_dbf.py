"""Тесты для DBF."""
import os
import tempfile
import shutil
from datetime import date
from decimal import Decimal
import pytest
from dbf_core import DBF, DBFError

@pytest.fixture
def temp_dir():
    """Временная директория для тестов."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)

@pytest.fixture
def sample_dbf(temp_dir):
    """Создать тестовый DBF с данными."""
    path = os.path.join(temp_dir, "test.dbf")
    fields = [
        ("ID", "N", 5, 0),
        ("NAME", "C", 20, 0),
        ("SUM", "N", 10, 2),
        ("DATE", "D", 8, 0),
        ("ACTIVE", "L", 1, 0),
    ]
    db = DBF.create(path, fields, "cp866")
    
    # Добавить тестовые записи
    db.append({"ID": 1, "NAME": "Иванов", "SUM": 100.50, "DATE": date(2024, 1, 15), "ACTIVE": True})
    db.append({"ID": 2, "NAME": "Петров", "SUM": 250.00, "DATE": date(2024, 2, 20), "ACTIVE": False})
    db.append({"ID": 3, "NAME": "Сидоров", "SUM": 75.25, "DATE": date(2024, 3, 10), "ACTIVE": True})
    db.append({"ID": 4, "NAME": "Кузнецов", "SUM": 500.00, "DATE": date(2024, 4, 5), "ACTIVE": True})
    db.append({"ID": 5, "NAME": "Смирнов", "SUM": 150.75, "DATE": date(2024, 5, 25), "ACTIVE": False})
    
    db.close()
    return path

@pytest.fixture
def sample_dbf_with_memo(temp_dir):
    """Создать тестовый DBF с мемо-полем."""
    path = os.path.join(temp_dir, "test_memo.dbf")
    fields = [
        ("ID", "N", 5, 0),
        ("NAME", "C", 20, 0),
        ("NOTES", "M", 10, 0),
    ]
    db = DBF.create(path, fields, "cp866", memo=True)
    
    db.append({"ID": 1, "NAME": "Иванов", "NOTES": "Первая запись"})
    db.append({"ID": 2, "NAME": "Петров", "NOTES": "Вторая запись с длинным текстом"})
    db.append({"ID": 3, "NAME": "Сидоров", "NOTES": "Третья"})
    
    db.close()
    return path

# ========== ZAP ==========

def test_zap_empty(temp_dir):
    """ZAP на пустом файле."""
    path = os.path.join(temp_dir, "empty.dbf")
    fields = [("ID", "N", 5, 0)]
    db = DBF.create(path, fields, "cp866")
    db.close()
    
    db = DBF(path, "rw")
    db.zap()
    assert db.record_count == 0
    db.close()

def test_zap_with_data(sample_dbf):
    """ZAP удаляет все записи."""
    db = DBF(sample_dbf, "rw")
    assert db.record_count == 5
    
    db.zap()
    assert db.record_count == 0
    
    # Проверить бэкап
    assert os.path.exists(sample_dbf + ".bak")
    
    # Проверить что файл пустой
    recs = list(db.records())
    assert len(recs) == 0
    db.close()

def test_zap_with_memo(sample_dbf_with_memo):
    """ZAP с мемо-файлом."""
    db = DBF(sample_dbf_with_memo, "rw")
    assert db.record_count == 3
    
    db.zap()
    assert db.record_count == 0
    
    # Мемо-файл должен быть пересоздан
    memo_path = os.path.splitext(sample_dbf_with_memo)[0] + ".dbt"
    assert os.path.exists(memo_path)
    assert os.path.exists(memo_path + ".bak")
    db.close()

# ========== PACK ==========

def test_pack_no_deleted(sample_dbf):
    """PACK без удалённых записей."""
    db = DBF(sample_dbf, "rw")
    initial_count = db.record_count
    
    db.pack()
    assert db.record_count == initial_count
    db.close()

def test_pack_with_deleted(sample_dbf):
    """PACK удаляет помеченные записи."""
    db = DBF(sample_dbf, "rw")
    
    # Пометить 2 записи
    db.set_deleted(1, True)
    db.set_deleted(3, True)
    
    db.pack()
    assert db.record_count == 3
    
    # Проверить что остались правильные записи
    recs = list(db.records())
    ids = [r.data["ID"] for r in recs]
    assert ids == [1, 3, 5]
    
    assert os.path.exists(sample_dbf + ".bak")
    db.close()

def test_pack_with_memo(sample_dbf_with_memo):
    """PACK с мемо-полями."""
    with DBF(sample_dbf_with_memo, "rw") as db:
        db.set_deleted(1, True)   # Петров
        db.pack()

        assert db.record_count == 2
        recs = list(db.records(load_memo=True))
        assert recs[0].data["NAME"] == "Иванов"
        assert recs[0].data["NOTES"] == "Первая запись"
        assert recs[1].data["NAME"] == "Сидоров"
        assert recs[1].data["NOTES"] == "Третья"

# ========== COPY TO ==========

def test_copy_all(sample_dbf, temp_dir):
    """COPY TO без фильтров."""
    dst = os.path.join(temp_dir, "copy_all.dbf")
    
    db = DBF(sample_dbf, "r")
    n = db.copy_to(dst)
    db.close()
    
    assert n == 5
    assert os.path.exists(dst)
    
    db2 = DBF(dst, "r")
    assert db2.record_count == 5
    db2.close()

def test_copy_fields(sample_dbf, temp_dir):
    """COPY TO с выбором полей."""
    dst = os.path.join(temp_dir, "copy_fields.dbf")
    
    db = DBF(sample_dbf, "r")
    n = db.copy_to(dst, fields=["ID", "NAME"])
    db.close()
    
    db2 = DBF(dst, "r")
    assert db2.field_names == ["ID", "NAME"]
    assert n == 5
    db2.close()

def test_copy_filter(sample_dbf, temp_dir):
    """COPY TO с фильтром."""
    dst = os.path.join(temp_dir, "copy_filter.dbf")

    with DBF(sample_dbf, "r") as db:
        n = db.copy_to(dst, filter_expr="SUM > 100")

    assert n == 4
    with DBF(dst, "r") as db2:
        for r in db2.records():
            assert r.data["SUM"] > 100

def test_copy_filter_py(sample_dbf, temp_dir):
    """COPY TO с Python-фильтром."""
    dst = os.path.join(temp_dir, "copy_filter_py.dbf")
    
    db = DBF(sample_dbf, "r")
    n = db.copy_to(dst, filter_py="ACTIVE and SUM >= 100")
    db.close()
    
    db2 = DBF(dst, "r")
    assert n == 2
    db2.close()

def test_copy_with_deleted(sample_dbf, temp_dir):
    """COPY TO с удалёнными записями."""
    db = DBF(sample_dbf, "rw")
    db.set_deleted(2, True)
    db.close()
    
    dst = os.path.join(temp_dir, "copy_deleted.dbf")
    db = DBF(sample_dbf, "r")
    n = db.copy_to(dst, include_deleted=True)
    db.close()
    
    db2 = DBF(dst, "r")
    assert n == 5
    assert db2.record_count == 5
    db2.close()

# ========== APPEND FROM ==========

def test_append_from_csv(sample_dbf, temp_dir):
    """APPEND FROM CSV."""
    csv_path = os.path.join(temp_dir, "import.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("ID;NAME;SUM;DATE;ACTIVE\n")
        f.write("10;Новиков;300.00;2024-06-01;T\n")
        f.write("11;Морозов;450.50;2024-07-15;F\n")
    
    db = DBF(sample_dbf, "rw")
    initial = db.record_count
    added, skipped = db.append_from(csv_path, is_csv=True)
    db.close()
    
    assert added == 2
    assert skipped == 0
    
    db2 = DBF(sample_dbf, "r")
    assert db2.record_count == initial + 2
    recs = list(db2.records())
    assert recs[-1].data["NAME"] == "Морозов"
    db2.close()

def test_append_from_csv_no_header(sample_dbf, temp_dir):
    """APPEND FROM CSV без заголовка."""
    csv_path = os.path.join(temp_dir, "import_no_header.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("20;Тестов;999.99;2024-08-01;T\n")
    
    db = DBF(sample_dbf, "rw")
    added, skipped = db.append_from(csv_path, is_csv=True, no_header=True)
    db.close()
    
    assert added == 1
    db2 = DBF(sample_dbf, "r")
    recs = list(db2.records())
    assert recs[-1].data["NAME"] == "Тестов"
    db2.close()

def test_append_from_csv_skip(sample_dbf, temp_dir):
    """APPEND FROM CSV с пропуском ошибок."""
    csv_path = os.path.join(temp_dir, "import_skip.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("ID;NAME;SUM\n")
        f.write("30;Хороший;100.00\n")
        f.write("31;Плохой;не_число\n")  # Ошибка в SUM
        f.write("32;Ещё_хороший;200.00\n")
    
    db = DBF(sample_dbf, "rw")
    added, skipped = db.append_from(csv_path, is_csv=True, skip=True)
    db.close()
    
    assert added == 2
    assert skipped == 1

def test_append_from_csv_force(sample_dbf, temp_dir):
    """APPEND FROM CSV с принудительным преобразованием."""
    csv_path = os.path.join(temp_dir, "import_force.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("ID;NAME;SUM\n")
        f.write("40;Тест;abc\n")  # SUM не число
    
    db = DBF(sample_dbf, "rw")
    added, skipped = db.append_from(csv_path, is_csv=True, force=True)
    db.close()
    
    assert added == 1
    db2 = DBF(sample_dbf, "r")
    recs = list(db2.records())
    assert recs[-1].data["SUM"] is None  # Преобразовано в None
    db2.close()

def test_append_from_dbf(sample_dbf, temp_dir):
    """APPEND FROM DBF."""
    src = os.path.join(temp_dir, "source.dbf")
    fields = [("ID", "N", 5, 0), ("NAME", "C", 20, 0), ("SUM", "N", 10, 2)]
    src_db = DBF.create(src, fields, "cp866")
    src_db.append({"ID": 50, "NAME": "Источник1", "SUM": 111.11})
    src_db.append({"ID": 51, "NAME": "Источник2", "SUM": 222.22})
    src_db.close()
    
    db = DBF(sample_dbf, "rw")
    initial = db.record_count
    added, skipped = db.append_from(src, is_csv=False)
    db.close()
    
    assert added == 2
    assert skipped == 0
    
    db2 = DBF(sample_dbf, "r")
    assert db2.record_count == initial + 2
    recs = list(db2.records())
    assert recs[-1].data["NAME"] == "Источник2"
    db2.close()

def test_append_from_dbf_memo_conflict(sample_dbf, sample_dbf_with_memo):
    """APPEND FROM DBF:.memo в источнике, приёмник без мемо."""
    with DBF(sample_dbf, "rw") as db:
        with pytest.raises(DBFError, match="мемо-поля"):
            db.append_from(sample_dbf_with_memo, is_csv=False)
        assert db.record_count == 5   # ничего не добавилось


def test_append_from_dbf_memo_force(sample_dbf, sample_dbf_with_memo):
    """--force: мемо-поля отбрасываются, остальное импортируется."""
    with DBF(sample_dbf, "rw") as db:
        added, skipped = db.append_from(sample_dbf_with_memo, is_csv=False, force=True)
    assert added == 3
    assert skipped == 0
    with DBF(sample_dbf, "r") as db:
        assert db.record_count == 8

if __name__ == "__main__":
    pytest.main([__file__, "-v"])