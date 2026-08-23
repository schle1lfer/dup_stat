"""Сохранение найденных дубликатов в локальные файлы (SQLite, pandas, Excel, JSON).

Как и остальные части проекта, экспорт построен на абстракции
ResultExporter (OCP — новый формат добавляется новым классом, ничего
не меняя в существующем коде) и общей вспомогательной функции
_rows_from_groups, чтобы не дублировать преобразование DuplicateGroup
в плоские строки (DRY).

ResultPersistence — единственное место, где генерируется timestamp,
поэтому все экспортёры одного запуска получают одну и ту же метку
времени и их файлы легко сопоставить друг с другом по имени.

Порядок строк здесь не переопределяется: DuplicateFinder.find() уже
возвращает группы отсортированными по размеру файла по убыванию (см.
finder.py), поэтому group_id=1 в сохранённых файлах всегда
соответствует самой крупной группе дубликатов.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Dict, Iterator, List

from .models import DuplicateGroup

TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


def make_timestamp() -> str:
    """Единая точка генерации метки времени для имён файлов результата."""
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _rows_from_groups(groups: List[DuplicateGroup]) -> Iterator[Dict]:
    """Разворачивает группы дубликатов (файлов и директорий) в плоские
    строки — общий формат данных, который переиспользуют все экспортёры.

    Это генератор (yield) — строки отдаются по одной, а не собираются
    сразу все в памяти.
    """
    for group_id, group in enumerate(groups, start=1):
        for record in group.records:  # у каждой группы — несколько записей-копий
            yield {
                "group_id": group_id,
                "kind": group.kind.value,  # "file" или "directory"
                "file_hash": record.file_hash,
                "path": str(record.path),  # Path -> строка, чтобы сохранить в БД/DataFrame
                "name": record.name,
                "size_bytes": record.size,
                "mtime": record.mtime,
                "wasted_bytes": group.wasted_size,
            }


def _build_dataframe(groups: List[DuplicateGroup]):
    """Общая часть для DataFrameResultExporter и ExcelResultExporter —
    оба сохраняют, по сути, одну и ту же таблицу, только в разные
    файлы (DRY: не дублируем построение DataFrame в двух местах)."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "Для сохранения результатов в pandas.DataFrame/Excel нужен пакет pandas. "
            "Установите его: pip install pandas"
        ) from exc
    return pd.DataFrame(list(_rows_from_groups(groups)))


class ResultExporter(ABC):
    """Сохраняет найденные группы дубликатов в файл на диске."""

    @abstractmethod
    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        """Сохраняет groups в output_dir и возвращает путь к созданному файлу."""
        raise NotImplementedError


class SqliteResultExporter(ResultExporter):
    """Сохраняет результаты в файл SQLite: '<prefix>_<timestamp>.sqlite3'.

    SQLite — это база данных, которая целиком хранится в одном файле,
    без отдельного сервера (в отличие от, например, PostgreSQL).
    Зависимостей не требует — sqlite3 входит в стандартную библиотеку Python.
    """

    def __init__(self, filename_prefix: str = "dup_stat_results"):
        self._filename_prefix = filename_prefix

    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)  # создать папку, если её ещё нет
        db_path = output_dir / f"{self._filename_prefix}_{timestamp}.sqlite3"

        # sqlite3.connect() открывает (или создаёт) файл базы данных.
        # "with ... as conn" гарантирует, что соединение будет закрыто
        # автоматически, даже если внутри случится ошибка.
        with sqlite3.connect(db_path) as conn:
            # CREATE TABLE — создаёт таблицу с описанными колонками и их типами.
            conn.execute(
                """
                CREATE TABLE duplicate_entries (
                    group_id     INTEGER NOT NULL,
                    kind         TEXT    NOT NULL,
                    file_hash    TEXT    NOT NULL,
                    path         TEXT    NOT NULL,
                    name         TEXT    NOT NULL,
                    size_bytes   INTEGER NOT NULL,
                    mtime        REAL    NOT NULL,
                    wasted_bytes INTEGER NOT NULL
                )
                """
            )
            # executemany() вставляет сразу много строк за один вызов —
            # быстрее, чем делать INSERT в цикле. :group_id и т.д. —
            # именованные "заглушки", которые sqlite3 сам подставит
            # значениями из словарей (защита от SQL-инъекций).
            conn.executemany(
                """
                INSERT INTO duplicate_entries
                    (group_id, kind, file_hash, path, name, size_bytes, mtime, wasted_bytes)
                VALUES
                    (:group_id, :kind, :file_hash, :path, :name, :size_bytes, :mtime, :wasted_bytes)
                """,
                list(_rows_from_groups(groups)),
            )
            conn.commit()  # сохраняет изменения на диск

        return db_path


class JsonResultExporter(ResultExporter):
    """Сохраняет результаты в файл JSON: '<prefix>_<timestamp>.json'.

    Не требует pandas — та же плоская таблица, что и в SQLite, просто
    в виде JSON-массива объектов (удобно для скриптов на любом языке,
    не только на Python).
    """

    def __init__(self, filename_prefix: str = "dup_stat_results"):
        self._filename_prefix = filename_prefix

    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{self._filename_prefix}_{timestamp}.json"

        rows = list(_rows_from_groups(groups))
        with json_path.open("w", encoding="utf-8") as f:
            # ensure_ascii=False — не превращать кириллицу в \uXXXX-escape;
            # indent=2 — читаемое форматирование с отступами.
            json.dump(rows, f, ensure_ascii=False, indent=2)

        return json_path


class DataFrameResultExporter(ResultExporter):
    """Сохраняет результаты как pandas.DataFrame: '<prefix>_<timestamp>.pkl'.

    Формат pickle выбран потому, что он сохраняет именно объект
    DataFrame (типы колонок и т.д.) — файл можно загрузить обратно
    одной командой `pandas.read_pickle(path)` без потери информации.
    """

    def __init__(self, filename_prefix: str = "dup_stat_results"):
        self._filename_prefix = filename_prefix

    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        df = _build_dataframe(groups)  # список словарей -> таблица pandas (бросит ImportError, если нет pandas)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        pkl_path = output_dir / f"{self._filename_prefix}_{timestamp}.pkl"

        df.to_pickle(pkl_path)  # сохраняем таблицу в файл

        return pkl_path


class ExcelResultExporter(ResultExporter):
    """Сохраняет результаты в Excel: '<prefix>_<timestamp>.xlsx'.

    Нужен и pandas (строит таблицу), и дополнительно пакет openpyxl
    (умеет писать сам файл .xlsx) — pandas сам его не включает.
    """

    def __init__(self, filename_prefix: str = "dup_stat_results"):
        self._filename_prefix = filename_prefix

    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        df = _build_dataframe(groups)  # бросит ImportError, если нет pandas

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = output_dir / f"{self._filename_prefix}_{timestamp}.xlsx"

        try:
            df.to_excel(xlsx_path, index=False, sheet_name="duplicates")
        except ImportError as exc:
            raise ImportError(
                "Для сохранения результатов в Excel нужен пакет openpyxl. "
                "Установите его: pip install openpyxl"
            ) from exc

        return xlsx_path


@dataclass
class PersistenceResult:
    """Что получилось при сохранении: какие файлы реально записаны, а
    какие форматы пришлось пропустить (например, из-за отсутствующей
    опциональной зависимости) — и почему."""

    saved: List[Path] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # человекочитаемые причины пропуска


class ResultPersistence:
    """Прогоняет несколько ResultExporter'ов за один вызов с общим timestamp.

    Если одному экспортёру не хватает опциональной зависимости
    (например, нет pandas или openpyxl), это не должно мешать сохранить
    результат в остальных доступных форматах — поэтому такой экспортёр
    просто пропускается (с понятной причиной в PersistenceResult.skipped),
    а не прерывает работу всех остальных.
    """

    def __init__(self, exporters: List[ResultExporter]):
        self._exporters = exporters

    def save_all(self, groups: List[DuplicateGroup], output_dir: Path) -> PersistenceResult:
        """Сохраняет один и тот же результат через все переданные
        экспортёры, с одинаковой меткой времени в именах файлов."""
        timestamp = make_timestamp()  # считаем один раз — на все экспортёры сразу
        output_dir = Path(output_dir)

        result = PersistenceResult()
        for exporter in self._exporters:
            try:
                result.saved.append(exporter.export(groups, output_dir, timestamp))
            except ImportError as exc:
                result.skipped.append(str(exc))
        return result
