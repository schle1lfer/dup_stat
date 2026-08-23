"""Рекурсивный обход директории и отбор файлов-кандидатов.

Обход построен на os.scandir, а не Path.rglob:
- DirEntry уже знает тип записи (файл/директория/симлинк) без
  дополнительного stat-вызова на большинстве платформ;
- stat() на размер файла делается только если реально нужен фильтр
  --min-size — в самом частом случае (min_size=0) он вообще не
  выполняется, что заметно быстрее на больших деревьях каталогов.

Обход итеративный, через явный стек, а не рекурсивные вызовы — не
упирается в лимит глубины рекурсии Python на сильно вложенных деревьях.
"""

from abc import ABC, abstractmethod
import os
from pathlib import Path
from typing import Iterator, List


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

        stack: List[str] = [str(root)]
        while stack:
            current = stack.pop()
            try:
                entries = os.scandir(current)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    try:
                        yield from self._handle_entry(entry, stack)
                    except OSError:
                        continue

    def _handle_entry(self, entry: "os.DirEntry", stack: List[str]) -> Iterator[Path]:
        if entry.is_symlink() and not self._follow_symlinks:
            return
        if entry.is_dir(follow_symlinks=self._follow_symlinks):
            stack.append(entry.path)
            return
        if not entry.is_file(follow_symlinks=self._follow_symlinks):
            return
        if self._min_size > 0:
            size = entry.stat(follow_symlinks=self._follow_symlinks).st_size
            if size < self._min_size:
                return
        yield Path(entry.path)
