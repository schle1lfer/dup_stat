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

find() всегда возвращает группы, отсортированные по размеру файла
по убыванию (внутри группы — по пути, для устойчивого порядка вне
зависимости от того, в каком порядке завершились потоки хеширования).
Это единственное место, где применяется сортировка — благодаря этому
она автоматически действует и в отчётах (reporting.py), и при
сохранении результатов (storage.py), без дублирования логики (DRY).

find_files() — то же самое, но вместе с промежуточными данными
(FileScanResult: все размеры и все реально посчитанные полные хеши).
Их переиспользует DirectoryDuplicateFinder (directory_finder.py) для
поиска директорий-дубликатов, не считая ни одного хеша повторно;
find() — просто тонкая обёртка над find_files() для обратной
совместимости и для случаев, когда директории не нужны.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .hash_computation import HashComputationStrategy, ThreadPoolHashComputation
from .hashing import FileHasher
from .matching import DuplicateKeyStrategy
from .models import DuplicateGroup, FileRecord, FileScanResult, sort_groups_by_size_desc
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
        # Все зависимости передаются снаружи (через аргументы __init__),
        # а не создаются внутри класса — это и есть Dependency Injection:
        # DuplicateFinder не привязан к конкретной реализации сканера/хешера.
        self._scanner = scanner
        self._hasher = hasher
        self._key_strategy = key_strategy
        # "or" здесь — если hash_computation не передали (None),
        # используем реализацию по умолчанию.
        self._hash_computation = hash_computation or ThreadPoolHashComputation()
        self._partial_hash_bytes = partial_hash_bytes

    def find(self, root: Path) -> List[DuplicateGroup]:
        """Простой способ получить только список групп-дубликатов."""
        return self.find_files(root).groups

    def find_files(self, root: Path) -> FileScanResult:
        """Полный поиск в три стадии (см. описание модуля выше)."""
        sizes = self._scan_sizes(root)                              # шаг 0: узнать размер каждого файла
        size_candidates = self._filter_by_size(sizes)                # стадия 1: отсеять уникальные размеры
        partial_candidates = self._filter_by_partial_hash(size_candidates, sizes)  # стадия 2
        records = self._build_records(partial_candidates)            # стадия 3: полный хеш
        groups = self._group_by_key(records)                         # собрать в группы дубликатов
        return FileScanResult(groups=groups, sizes=sizes, hashed_records=records)

    def _scan_sizes(self, root: Path) -> Dict[Path, int]:
        """Обходит директорию и запоминает размер каждого файла: {путь: размер}."""
        sizes: Dict[Path, int] = {}
        for path in self._scanner.scan(root):
            try:
                sizes[path] = path.stat().st_size
            except OSError:
                continue  # файл мог исчезнуть между сканированием и этой строкой — пропускаем
        return sizes

    @staticmethod  # не использует self — не зависит от состояния конкретного объекта
    def _filter_by_size(sizes: Dict[Path, int]) -> List[Path]:
        """Оставляет только файлы, у которых размер совпадает хотя бы
        ещё с одним файлом — остальные точно не могут быть дубликатами."""
        by_size = group_by(sizes.keys(), key_fn=lambda p: sizes[p])
        return drop_singleton_groups(by_size)

    def _filter_by_partial_hash(self, paths: List[Path], sizes: Dict[Path, int]) -> List[Path]:
        """Дополнительно отсеивает файлы по хешу первых байт — дешевле,
        чем сразу читать и хешировать файл целиком."""
        if not paths or self._partial_hash_bytes <= 0:
            return paths  # стадия отключена (partial_hash_bytes=0) или нечего проверять

        partial_hashes = self._hash_computation.compute_many(
            paths, self._hasher, max_bytes=self._partial_hash_bytes
        )
        readable_paths = list(partial_hashes.keys())
        # Группируем по паре (размер, хеш первых байт) — совпадать должно и то, и другое.
        by_partial: Dict[Tuple[int, str], List[Path]] = group_by(
            readable_paths, key_fn=lambda p: (sizes[p], partial_hashes[p])
        )
        return drop_singleton_groups(by_partial)

    def _build_records(self, paths: List[Path]) -> List[FileRecord]:
        """Считает полный хеш для оставшихся кандидатов и оборачивает
        каждый файл в FileRecord (со всеми его атрибутами)."""
        hashes = self._hash_computation.compute_many(paths, self._hasher, max_bytes=None)
        records: List[FileRecord] = []
        for path, file_hash in hashes.items():
            try:
                records.append(FileRecord.build(path, file_hash))
            except OSError:
                continue
        return records

    def _group_by_key(self, records: List[FileRecord]) -> List[DuplicateGroup]:
        """Финальный шаг: объединяет записи с одинаковым ключом
        (см. matching.py) в группы дубликатов."""
        groups = group_by(records, key_fn=self._key_strategy.key)
        duplicate_groups = [
            DuplicateGroup(key=key, records=sorted(recs, key=lambda r: str(r.path)))
            for key, recs in groups.items()
            if len(recs) > 1  # группа из одного файла — это не дубликат
        ]
        return sort_groups_by_size_desc(duplicate_groups)
