"""Модели данных: описание файла, группы дубликатов и результата сканирования."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple


class EntryKind(str, Enum):
    """Что представляет собой запись в группе дубликатов — файл или директория.

    (str, Enum) — значит, что EntryKind.FILE ведёт себя и как обычная
    строка "file" тоже (это удобно при сохранении в JSON/SQLite).
    """

    FILE = "file"
    DIRECTORY = "directory"


# @dataclass — декоратор, который сам генерирует __init__, __repr__ и
# сравнение по полям, чтобы не писать это вручную для простого класса-данных.
@dataclass(frozen=True)  # frozen=True — объект нельзя изменить после создания
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

    # Поля дата-класса: просто перечисляем имя и тип, значения задаются при создании объекта.
    path: Path
    name: str
    size: int
    mtime: float  # время последнего изменения файла (Unix timestamp)
    file_hash: str

    @classmethod
    def build(cls, path: Path, file_hash: str) -> "FileRecord":
        """Удобный конструктор: сам достаёт size/mtime/name из файла на диске,
        вызывающему коду достаточно передать путь и уже готовый хеш."""
        stat = path.stat()  # системный вызов, возвращающий размер, время изменения и т.д.
        return cls(
            path=path,
            name=path.name,
            size=stat.st_size,
            mtime=stat.st_mtime,
            file_hash=file_hash,
        )


@dataclass  # не frozen — список records пополняется после создания группы
class DuplicateGroup:
    """Группа файлов или директорий, признанных дубликатами друг друга."""

    key: Tuple  # значение, по которому все записи в группе считаются одинаковыми
    records: List[FileRecord] = field(default_factory=list)  # список найденных копий
    kind: EntryKind = EntryKind.FILE  # по умолчанию группа файловая, если не указано иное

    @property  # позволяет обращаться как group.size_per_copy, без скобок ()
    def size_per_copy(self) -> int:
        """Размер одной копии (у всех записей в группе он одинаковый)."""
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
    # key=... говорит, по какому значению сравнивать группы;
    # reverse=True — от большего к меньшему.
    return sorted(groups, key=lambda g: g.size_per_copy, reverse=True)


@dataclass
class FileScanResult:
    """Результат файлового поиска дубликатов вместе с промежуточными
    данными, которые нужны DirectoryDuplicateFinder, чтобы не считать
    хеши файлов повторно (см. directory_finder.py)."""

    groups: List[DuplicateGroup]  # найденные группы файловых дубликатов
    sizes: Dict[Path, int]  # размер каждого просканированного файла: {путь: размер}
    hashed_records: List[FileRecord]  # все файлы, для которых был посчитан полный хеш
