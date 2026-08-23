"""Стратегии вычисления хешей для набора файлов сразу.

Чтение и хеширование файлов — I/O-bound работа (плюс hashlib
освобождает GIL на крупных чанках), поэтому пул потоков даёт реальное
ускорение на директориях с большим количеством файлов, особенно на
SSD/сетевых дисках, где диск может обслуживать несколько запросов на
чтение параллельно.

DuplicateFinder работает только с абстракцией HashComputationStrategy
(DIP) и не знает, считаются хеши последовательно или параллельно —
это можно поменять, не трогая логику поиска (OCP).

К моменту вызова compute_many() список paths уже точно известен (это
результат фильтрации на предыдущих стадиях, см. finder.py) — то есть
"общее количество файлов" для прогресс-бара уже есть заранее, без
отдельного прохода. Поэтому прогресс (progress.py) подключается именно
здесь: start(len(paths)) в начале и advance() после каждого файла.
"""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed  # инструменты для работы с пулом потоков
from pathlib import Path
from typing import Dict, Iterable, Optional

from .hashing import FileHasher
from .progress import NullProgressReporter, ProgressReporter


class HashComputationStrategy(ABC):
    """Интерфейс: "посчитать хеши для списка файлов". Как именно —
    последовательно или параллельно — решает конкретная реализация."""

    @abstractmethod
    def compute_many(
        self,
        paths: Iterable[Path],
        hasher: FileHasher,
        max_bytes: Optional[int] = None,
        label: str = "",
    ) -> Dict[Path, str]:
        """Считает хеш для каждого пути. Файлы, которые не удалось
        прочитать (OSError), молча пропускаются. Возвращает {path: hash}.

        label — подпись для индикатора прогресса (например, "полный хеш"),
        ни на что кроме отображения не влияет.
        """
        raise NotImplementedError


class ThreadPoolHashComputation(HashComputationStrategy):
    """Хеширует файлы в пуле потоков.

    max_workers=1 — выполняется последовательно, без накладных расходов
    на создание пула. max_workers=None — авторазмер пула, как в
    стандартном ThreadPoolExecutor (обычно min(32, cpu_count + 4)).
    """

    def __init__(self, max_workers: Optional[int] = None, progress: Optional[ProgressReporter] = None):
        if max_workers is not None and max_workers < 1:
            raise ValueError("max_workers должен быть положительным")
        self._max_workers = max_workers
        # По умолчанию — "ничего не делать": прогресс не навязывается
        # тем, кто использует пакет программно, а не через CLI.
        self._progress = progress or NullProgressReporter()

    def compute_many(
        self,
        paths: Iterable[Path],
        hasher: FileHasher,
        max_bytes: Optional[int] = None,
        label: str = "",
    ) -> Dict[Path, str]:
        paths = list(paths)  # на случай, если пришёл "одноразовый" итератор — сохраняем как список
        if not paths:
            return {}

        self._progress.start(len(paths), label)
        try:
            if self._max_workers == 1:
                return self._compute_sequential(paths, hasher, max_bytes)
            return self._compute_parallel(paths, hasher, max_bytes)
        finally:
            self._progress.finish()

    def _compute_parallel(
        self, paths, hasher: FileHasher, max_bytes: Optional[int]
    ) -> Dict[Path, str]:
        results: Dict[Path, str] = {}
        # ThreadPoolExecutor — пул рабочих потоков: задачи распределяются
        # между несколькими потоками и выполняются одновременно.
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            # Запускаем хеширование каждого файла как отдельную задачу.
            # executor.submit() не ждёт результата — сразу возвращает
            # "будущее" (future), результат появится в нём позже.
            future_to_path = {
                executor.submit(hasher.compute, path, max_bytes): path for path in paths
            }
            # as_completed() отдаёт задачи по мере их завершения
            # (в порядке готовности, а не в порядке запуска).
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    results[path] = future.result()  # результат работы hasher.compute() для этого файла
                except OSError:
                    pass  # файл исчез/недоступен — просто пропускаем его
                finally:
                    self._progress.advance()  # отмечаем файл обработанным в любом случае
        return results

    def _compute_sequential(self, paths, hasher: FileHasher, max_bytes: Optional[int]) -> Dict[Path, str]:
        results: Dict[Path, str] = {}
        for path in paths:  # обычный цикл — один файл за другим, без потоков
            try:
                results[path] = hasher.compute(path, max_bytes=max_bytes)
            except OSError:
                pass
            finally:
                self._progress.advance()
        return results
