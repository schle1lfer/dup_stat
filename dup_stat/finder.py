"""Основная логика поиска дубликатов.

DuplicateFinder ничего не знает о конкретном способе обхода директории,
вычисления хеша, стратегии параллелизма или критерии сравнения — все
эти зависимости передаются через конструктор (Dependency Inversion) в
виде абстракций из scanning.py / hashing.py / hash_computation.py /
matching.py.

Поиск идёт в три стадии, и каждая следующая работает только с
кандидатами, прошедшими предыдущую — это главная оптимизация скорости
на больших директориях, где полное чтение и хеширование каждого файла
было бы намного дороже:

  1. Группировка по размеру файла (дешёвая операция — только stat).
     Файл с уникальным в директории размером не может иметь дубликат
     по содержимому, поэтому дальше не идёт.
  2. Группировка по хешу небольшого префикса файла (partial hash).
     Разные файлы почти всегда отличаются уже в первых байтах, так
     что это отсеивает "случайных соседей по размеру" без чтения
     файла целиком — особенно выгодно на больших файлах.
  3. Полный хеш — только для того, что осталось после стадий 1-2,
     то есть для реальных кандидатов в дубликаты.

Хеширование на стадиях 2 и 3 может выполняться параллельно (пул
потоков, см. hash_computation.py).
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .hash_computation import HashComputationStrategy, ThreadPoolHashComputation
from .hashing import FileHasher
from .matching import DuplicateKeyStrategy
from .models import DuplicateGroup, FileRecord
from .scanning import FileScanner
from .utils import drop_singleton_groups, group_by

DEFAULT_PARTIAL_HASH_BYTES = 64 * 1024  # 64 КБ обычно достаточно, чтобы отличить разные файлы


class DuplicateFinder:
    def __init__(
        self,
        scanner: FileScanner,
        hasher: FileHasher,
        key_strategy: DuplicateKeyStrategy,
        hash_computation: Optional[HashComputationStrategy] = None,
        partial_hash_bytes: int = DEFAULT_PARTIAL_HASH_BYTES,
    ):
        self._scanner = scanner
        self._hasher = hasher
        self._key_strategy = key_strategy
        self._hash_computation = hash_computation or ThreadPoolHashComputation()
        self._partial_hash_bytes = partial_hash_bytes

    def find(self, root: Path) -> List[DuplicateGroup]:
        sizes = self._scan_sizes(root)
        size_candidates = self._filter_by_size(sizes)
        partial_candidates = self._filter_by_partial_hash(size_candidates, sizes)
        records = self._build_records(partial_candidates)
        return self._group_by_key(records)

    def _scan_sizes(self, root: Path) -> Dict[Path, int]:
        sizes: Dict[Path, int] = {}
        for path in self._scanner.scan(root):
            try:
                sizes[path] = path.stat().st_size
            except OSError:
                continue
        return sizes

    @staticmethod
    def _filter_by_size(sizes: Dict[Path, int]) -> List[Path]:
        by_size = group_by(sizes.keys(), key_fn=lambda p: sizes[p])
        return drop_singleton_groups(by_size)

    def _filter_by_partial_hash(self, paths: List[Path], sizes: Dict[Path, int]) -> List[Path]:
        if not paths or self._partial_hash_bytes <= 0:
            return paths

        partial_hashes = self._hash_computation.compute_many(
            paths, self._hasher, max_bytes=self._partial_hash_bytes
        )
        readable_paths = list(partial_hashes.keys())
        by_partial: Dict[Tuple[int, str], List[Path]] = group_by(
            readable_paths, key_fn=lambda p: (sizes[p], partial_hashes[p])
        )
        return drop_singleton_groups(by_partial)

    def _build_records(self, paths: List[Path]) -> List[FileRecord]:
        hashes = self._hash_computation.compute_many(paths, self._hasher, max_bytes=None)
        records: List[FileRecord] = []
        for path, file_hash in hashes.items():
            try:
                records.append(FileRecord.build(path, file_hash))
            except OSError:
                continue
        return records

    def _group_by_key(self, records: List[FileRecord]) -> List[DuplicateGroup]:
        groups = group_by(records, key_fn=self._key_strategy.key)
        return [
            DuplicateGroup(key=key, records=recs)
            for key, recs in groups.items()
            if len(recs) > 1
        ]
