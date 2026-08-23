"""Вычисление хешсумм файлов.

Абстракция FileHasher позволяет подменить алгоритм хеширования (OCP) —
например, добавить более быстрый некриптографический хеш, не трогая
остальной код.
"""

from abc import ABC, abstractmethod
import hashlib
from pathlib import Path

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 МБ — не грузим весь файл в память


class FileHasher(ABC):
    """Интерфейс вычислителя хешсуммы файла."""

    @abstractmethod
    def compute(self, path: Path) -> str:
        """Возвращает хеш содержимого файла в виде hex-строки."""
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

    def compute(self, path: Path) -> str:
        hasher = hashlib.new(self._algorithm)
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(self._chunk_size), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
