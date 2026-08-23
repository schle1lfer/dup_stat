"""Вычисление хешсумм файлов.

Абстракция FileHasher позволяет подменить алгоритм хеширования (OCP) —
например, добавить более быстрый некриптографический хеш, не трогая
остальной код.

compute() умеет считать хеш не только всего файла, но и его префикса
(max_bytes) — один и тот же метод используется и для быстрого
предварительного хеша (см. finder.py), и для полного, вместо двух
почти одинаковых реализаций (DRY).
"""

from abc import ABC, abstractmethod
import hashlib
from pathlib import Path
from typing import Optional

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 МБ — не грузим весь файл в память


class FileHasher(ABC):
    """Интерфейс вычислителя хешсуммы файла."""

    @abstractmethod
    def compute(self, path: Path, max_bytes: Optional[int] = None) -> str:
        """Возвращает хеш содержимого файла в виде hex-строки.

        Если max_bytes задан, хешируются только первые max_bytes байт
        файла (используется для быстрого предварительного отсева).
        """
        raise NotImplementedError


class HashlibFileHasher(FileHasher):
    """Хешер поверх стандартного hashlib, читает файл потоково блоками."""

    def __init__(self, algorithm: str = "sha256", chunk_size: int = DEFAULT_CHUNK_SIZE):
        if algorithm not in hashlib.algorithms_available:
            raise ValueError(f"Неподдерживаемый алгоритм хеширования: {algorithm!r}")
        if chunk_size <= 0:
            raise ValueError("chunk_size должен быть положительным")
        self._algorithm = algorithm
        self._chunk_size = chunk_size

    @property
    def algorithm(self) -> str:
        return self._algorithm

    def compute(self, path: Path, max_bytes: Optional[int] = None) -> str:
        hasher = hashlib.new(self._algorithm)
        remaining = max_bytes
        with path.open("rb") as f:
            while remaining is None or remaining > 0:
                read_size = self._chunk_size if remaining is None else min(self._chunk_size, remaining)
                chunk = f.read(read_size)
                if not chunk:
                    break
                hasher.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
        return hasher.hexdigest()
