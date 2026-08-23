"""Рекурсивный обход директории и отбор файлов-кандидатов."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator


class FileScanner(ABC):
    """Интерфейс источника файлов для последующего анализа."""

    @abstractmethod
    def scan(self, root: Path) -> Iterator[Path]:
        """Рекурсивно возвращает пути ко всем подходящим файлам в root."""
        raise NotImplementedError


class RecursiveFileScanner(FileScanner):
    """Обходит директорию рекурсивно, отфильтровывая нерелевантные записи."""

    def __init__(self, follow_symlinks: bool = False, min_size: int = 0):
        self._follow_symlinks = follow_symlinks
        self._min_size = min_size

    def scan(self, root: Path) -> Iterator[Path]:
        root = Path(root)
        if not root.is_dir():
            raise NotADirectoryError(f"{root} не является директорией")

        for path in root.rglob("*"):
            if path.is_symlink() and not self._follow_symlinks:
                continue
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size < self._min_size:
                continue
            yield path
