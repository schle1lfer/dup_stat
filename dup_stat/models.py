"""Модели данных: описание файла, группы дубликатов и результата сканирования."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple


class EntryKind(str, Enum):
    """Что представляет собой запись в группе дубликатов — файл или директория."""

    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class FileRecord:
    """Снимок атрибутов одной записи (файла или директории), нужных для
    сравнения на дубликаты и вывода в отчёт.

    Для директорий используется та же структура: path/name — самой
    директории, size — суммарный размер всех файлов внутри неё
    рекурсивно, file_hash — не хеш содержимого одного файла, а сигнатура
    всего поддерева (см. directory_finder.py). Это позволяет остальному
    коду (reporting.py, storage.py) работать с файлами и директориями
    единообразно, не зная о разнице (DRY).
    """

    path: Path
    name: str
    size: int
    mtime: float
    file_hash: str

    @classmethod
    def build(cls, path: Path, file_hash: str) -> "FileRecord":
        stat = path.stat()
        return cls(
            path=path,
            name=path.name,
            size=stat.st_size,
            mtime=stat.st_mtime,
            file_hash=file_hash,
        )


@dataclass
class DuplicateGroup:
    """Группа файлов или директорий, признанных дубликатами друг друга."""

    key: Tuple
    records: List[FileRecord] = field(default_factory=list)
    kind: EntryKind = EntryKind.FILE

    @property
    def size_per_copy(self) -> int:
        return self.records[0].size if self.records else 0

    @property
    def total_size(self) -> int:
        """Суммарный размер всех копий (включая оригинал)."""
        return sum(r.size for r in self.records)

    @property
    def wasted_size(self) -> int:
        """Размер, который можно освободить, оставив только одну копию."""
        if len(self.records) < 2:
            return 0
        return self.size_per_copy * (len(self.records) - 1)


def sort_groups_by_size_desc(groups: List[DuplicateGroup]) -> List[DuplicateGroup]:
    """Единая точка сортировки результата — по размеру копии, по убыванию.

    Используется и для файловых, и для директорийных групп, чтобы при
    объединении результатов (см. cli.py) не было двух разных мест,
    решающих, в каком порядке идёт вывод (DRY).
    """
    return sorted(groups, key=lambda g: g.size_per_copy, reverse=True)


@dataclass
class FileScanResult:
    """Результат файлового поиска дубликатов вместе с промежуточными
    данными, которые нужны DirectoryDuplicateFinder, чтобы не считать
    хеши файлов повторно (см. directory_finder.py)."""

    groups: List[DuplicateGroup]
    sizes: Dict[Path, int]
    hashed_records: List[FileRecord]
