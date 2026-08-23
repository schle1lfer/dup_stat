"""Стратегии вычисления хешей для набора файлов сразу.

Чтение и хеширование файлов — I/O-bound работа (плюс hashlib
освобождает GIL на крупных чанках), поэтому пул потоков даёт реальное
ускорение на директориях с большим количеством файлов, особенно на
SSD/сетевых дисках, где диск может обслуживать несколько запросов на
чтение параллельно.

DuplicateFinder работает только с абстракцией HashComputationStrategy
(DIP) и не знает, считаются хеши последовательно или параллельно —
это можно поменять, не трогая логику поиска (OCP).
"""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, Optional

from .hashing import FileHasher


class HashComputationStrategy(ABC):
    @abstractmethod
    def compute_many(
        self,
        paths: Iterable[Path],
        hasher: FileHasher,
        max_bytes: Optional[int] = None,
    ) -> Dict[Path, str]:
        """Считает хеш для каждого пути. Файлы, которые не удалось
        прочитать (OSError), молча пропускаются. Возвращает {path: hash}."""
        raise NotImplementedError


class ThreadPoolHashComputation(HashComputationStrategy):
    """Хеширует файлы в пуле потоков.

    max_workers=1 — выполняется последовательно, без накладных расходов
    на создание пула. max_workers=None — авторазмер пула, как в
    стандартном ThreadPoolExecutor (обычно min(32, cpu_count + 4)).
    """

    def __init__(self, max_workers: Optional[int] = None):
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers должен быть положительным")
        self._max_workers = max_workers

    def compute_many(
        self,
        paths: Iterable[Path],
        hasher: FileHasher,
        max_bytes: Optional[int] = None,
    ) -> Dict[Path, str]:
        paths = list(paths)
        if not paths:
            return {}
        if self._max_workers == 1:
            return self._compute_sequential(paths, hasher, max_bytes)
        return self._compute_parallel(paths, hasher, max_bytes)

    def _compute_parallel(
        self, paths, hasher: FileHasher, max_bytes: Optional[int]
    ) -> Dict[Path, str]:
        results: Dict[Path, str] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_path = {
                executor.submit(hasher.compute, path, max_bytes): path for path in paths
            }
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    results[path] = future.result()
                except OSError:
                    continue
        return results

    @staticmethod
    def _compute_sequential(paths, hasher: FileHasher, max_bytes: Optional[int]) -> Dict[Path, str]:
        results: Dict[Path, str] = {}
        for path in paths:
            try:
                results[path] = hasher.compute(path, max_bytes=max_bytes)
            except OSError:
                continue
        return results
