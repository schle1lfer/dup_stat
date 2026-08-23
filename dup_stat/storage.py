"""Сохранение найденных дубликатов в локальные файлы (SQLite, pandas).

Как и остальные части проекта, экспорт построен на абстракции
ResultExporter (OCP — новый формат добавляется новым классом, ничего
не меняя в существующем коде) и общей вспомогательной функции
_rows_from_groups, чтобы не дублировать преобразование DuplicateGroup
в плоские строки (DRY).

ResultPersistence — единственное место, где генерируется timestamp,
поэтому все экспортёры одного запуска получают одну и ту же метку
времени и их файлы легко сопоставить друг с другом по имени.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Dict, Iterator, List

from .models import DuplicateGroup

TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


def make_timestamp() -> str:
    """Единая точка генерации метки времени для имён файлов результата."""
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def _rows_from_groups(groups: List[DuplicateGroup]) -> Iterator[Dict]:
    """Разворачивает группы дубликатов в плоские строки — общий формат
    данных, который переиспользуют все экспортёры."""
    for group_id, group in enumerate(groups, start=1):
        for record in group.records:
            yield {
                "group_id": group_id,
                "file_hash": record.file_hash,
                "path": str(record.path),
                "name": record.name,
                "size_bytes": record.size,
                "mtime": record.mtime,
                "wasted_bytes": group.wasted_size,
            }


class ResultExporter(ABC):
    """Сохраняет найденные группы дубликатов в файл на диске."""

    @abstractmethod
    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        """Сохраняет groups в output_dir и возвращает путь к созданному файлу."""
        raise NotImplementedError


class SqliteResultExporter(ResultExporter):
    """Сохраняет результаты в файл SQLite: '<prefix>_<timestamp>.sqlite3'."""

    def __init__(self, filename_prefix: str = "dup_stat_results"):
        self._filename_prefix = filename_prefix

    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        db_path = output_dir / f"{self._filename_prefix}_{timestamp}.sqlite3"

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE duplicate_files (
                    group_id     INTEGER NOT NULL,
                    file_hash    TEXT    NOT NULL,
                    path         TEXT    NOT NULL,
                    name         TEXT    NOT NULL,
                    size_bytes   INTEGER NOT NULL,
                    mtime        REAL    NOT NULL,
                    wasted_bytes INTEGER NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO duplicate_files
                    (group_id, file_hash, path, name, size_bytes, mtime, wasted_bytes)
                VALUES
                    (:group_id, :file_hash, :path, :name, :size_bytes, :mtime, :wasted_bytes)
                """,
                list(_rows_from_groups(groups)),
            )
            conn.commit()

        return db_path


class DataFrameResultExporter(ResultExporter):
    """Сохраняет результаты как pandas.DataFrame: '<prefix>_<timestamp>.pkl'.

    Формат pickle выбран потому, что он сохраняет именно объект
    DataFrame (типы колонок и т.д.) — файл можно загрузить обратно
    одной командой `pandas.read_pickle(path)` без потери информации.
    """

    def __init__(self, filename_prefix: str = "dup_stat_results"):
        self._filename_prefix = filename_prefix

    def export(self, groups: List[DuplicateGroup], output_dir: Path, timestamp: str) -> Path:
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "Для сохранения результатов в pandas.DataFrame нужен пакет pandas. "
                "Установите его: pip install pandas"
            ) from exc

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        pkl_path = output_dir / f"{self._filename_prefix}_{timestamp}.pkl"

        df = pd.DataFrame(list(_rows_from_groups(groups)))
        df.to_pickle(pkl_path)

        return pkl_path


class ResultPersistence:
    """Прогоняет несколько ResultExporter'ов за один вызов с общим timestamp."""

    def __init__(self, exporters: List[ResultExporter]):
        self._exporters = exporters

    def save_all(self, groups: List[DuplicateGroup], output_dir: Path) -> List[Path]:
        timestamp = make_timestamp()
        output_dir = Path(output_dir)
        return [exporter.export(groups, output_dir, timestamp) for exporter in self._exporters]
