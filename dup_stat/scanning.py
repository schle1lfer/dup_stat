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
        """Рекурсивно возвращает пути ко всем подходящим файлам в root.

        Это генератор (используется yield) — файлы отдаются по одному,
        а не собираются все сразу в список. Так экономится память на
        больших директориях.
        """
        raise NotImplementedError


class RecursiveFileScanner(FileScanner):
    """Обходит директорию рекурсивно, отфильтровывая нерелевантные записи."""

    def __init__(self, follow_symlinks: bool = False, min_size: int = 0):
        self._follow_symlinks = follow_symlinks  # переходить ли по символическим ссылкам
        self._min_size = min_size  # игнорировать файлы меньше этого размера (в байтах)

    def scan(self, root: Path) -> Iterator[Path]:
        root = Path(root)
        if not root.is_dir():
            raise NotADirectoryError(f"{root} не является директорией")

        # Стек директорий, которые ещё нужно просмотреть. Начинаем с
        # корневой. "Стек" — список, из которого берём и кладём с одного
        # конца (через .pop()/.append()) — так реализуется обход без
        # рекурсивных вызовов функции.
        stack: List[str] = [str(root)]
        while stack:  # пока есть необойдённые директории
            current = stack.pop()  # достаём последнюю добавленную директорию
            try:
                entries = os.scandir(current)  # список файлов/папок внутри current
            except OSError:
                continue  # нет доступа к директории — пропускаем её
            with entries:  # закрывает системный дескриптор автоматически по выходу из блока
                for entry in entries:
                    try:
                        # yield from — "переливает" все пути, отданные
                        # _handle_entry, наружу из текущего генератора.
                        yield from self._handle_entry(entry, stack)
                    except OSError:
                        continue

    def _handle_entry(self, entry: "os.DirEntry", stack: List[str]) -> Iterator[Path]:
        """Решает, что делать с одной записью (файл/папка/ссылка):
        добавить директорию в очередь на обход или отдать файл наружу."""
        if entry.is_symlink() and not self._follow_symlinks:
            return  # символическая ссылка, и следовать по ней не просили — пропускаем
        if entry.is_dir(follow_symlinks=self._follow_symlinks):
            stack.append(entry.path)  # это папка — добавляем в очередь на обход позже
            return
        if not entry.is_file(follow_symlinks=self._follow_symlinks):
            return  # не файл и не папка (например, сокет) — пропускаем
        if self._min_size > 0:
            size = entry.stat(follow_symlinks=self._follow_symlinks).st_size
            if size < self._min_size:
                return  # слишком маленький файл — не интересен
        yield Path(entry.path)  # отдаём найденный файл наружу
