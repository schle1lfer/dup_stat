"""Модели данных: описание файла и группы дубликатов."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


@dataclass(frozen=True)
class FileRecord:
    """Снимок атрибутов одного файла, нужных для сравнения на дубликаты."""

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
    """Группа файлов, признанных дубликатами друг друга по выбранному критерию."""

    key: Tuple
    records: List[FileRecord] = field(default_factory=list)

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
