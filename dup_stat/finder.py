"""Основная логика поиска дубликатов.

DuplicateFinder ничего не знает о конкретном способе обхода директории,
вычисления хеша или критерии сравнения — он получает эти зависимости
через конструктор (Dependency Inversion) в виде абстракций из
scanning.py / hashing.py / matching.py. Это позволяет подменять любую
часть (например, взять другой алгоритм хеширования или другой критерий
совпадения) не меняя код самого поиска.
"""

from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from .hashing import FileHasher
from .matching import DuplicateKeyStrategy
from .models import DuplicateGroup, FileRecord
from .scanning import FileScanner


class DuplicateFinder:
    def __init__(
        self,
        scanner: FileScanner,
        hasher: FileHasher,
        key_strategy: DuplicateKeyStrategy,
    ):
        self._scanner = scanner
        self._hasher = hasher
        self._key_strategy = key_strategy

    def find(self, root: Path) -> List[DuplicateGroup]:
        candidates = self._group_candidates_by_size(root)
        records = self._build_records(candidates)
        return self._group_by_key(records)

    def _group_candidates_by_size(self, root: Path) -> List[Path]:
        """Файлы с уникальным по всей директории размером не могут быть
        дубликатами по содержимому, так что их можно не хешировать вовсе —
        это существенно ускоряет работу на больших директориях."""
        by_size: Dict[int, List[Path]] = defaultdict(list)
        for path in self._scanner.scan(root):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            by_size[size].append(path)

        candidates: List[Path] = []
        for paths in by_size.values():
            if len(paths) > 1:
                candidates.extend(paths)
        return candidates

    def _build_records(self, paths: List[Path]) -> List[FileRecord]:
        records: List[FileRecord] = []
        for path in paths:
            try:
                file_hash = self._hasher.compute(path)
                records.append(FileRecord.build(path, file_hash))
            except OSError:
                continue
        return records

    def _group_by_key(self, records: List[FileRecord]) -> List[DuplicateGroup]:
        groups: Dict[tuple, DuplicateGroup] = {}
        for record in records:
            key = self._key_strategy.key(record)
            group = groups.setdefault(key, DuplicateGroup(key=key))
            group.records.append(record)
        return [group for group in groups.values() if len(group.records) > 1]
