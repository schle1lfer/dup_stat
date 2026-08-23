"""Индикатор прогресса для длительных операций (хеширование файлов).

ProgressReporter — абстракция (как и везде в проекте, OCP/DIP):
ThreadPoolHashComputation (hash_computation.py) работает только с этим
интерфейсом и не знает, рисуется ли что-то в терминале вообще.

- NullProgressReporter — реализация "ничего не делать". Используется по
  умолчанию — например, при программном использовании пакета из кода
  или в тестах, где индикатор в терминале не нужен.
- SpinnerProgressReporter — реализация для CLI: крутящееся колёсико +
  проценты + "N из M файлов" на одной обновляющейся строке.

Прогресс считается по количеству файлов, а не по байтам: после стадий
фильтрации (finder.py) точное число файлов, которые предстоит
прохешировать на каждой стадии, уже известно заранее — это и есть
"общее количество", относительно которого считаются проценты.
"""

from abc import ABC, abstractmethod
import sys
import threading  # хеширование идёт в нескольких потоках — нужна защита от гонки


class ProgressReporter(ABC):
    """Интерфейс индикатора прогресса длительной операции из N шагов."""

    @abstractmethod
    def start(self, total: int, label: str = "") -> None:
        """Начинает новую операцию из total шагов (например, файлов)."""
        raise NotImplementedError

    @abstractmethod
    def advance(self, step: int = 1) -> None:
        """Отмечает, что выполнено ещё step шагов."""
        raise NotImplementedError

    @abstractmethod
    def finish(self) -> None:
        """Завершает текущую операцию (например, переводит строку)."""
        raise NotImplementedError


class NullProgressReporter(ProgressReporter):
    """Ничего не делает — безопасный вариант по умолчанию."""

    def start(self, total: int, label: str = "") -> None:
        pass

    def advance(self, step: int = 1) -> None:
        pass

    def finish(self) -> None:
        pass


class SpinnerProgressReporter(ProgressReporter):
    """Текстовый индикатор в терминале: крутящееся колёсико + проценты +
    "N из M файлов", всё на одной обновляющейся строке (перевод строки
    "\\r" возвращает курсор в начало и следующая запись перерисовывает
    ту же строку поверх старой).

    Потокобезопасен: advance() может вызываться из разных потоков сразу
    (хеширование выполняется в пуле потоков, см. hash_computation.py).
    """

    _SPINNER_FRAMES = "|/-\\"  # 4 кадра простого ASCII-колёсика, по кругу

    def __init__(self, stream=sys.stderr):
        self._stream = stream
        self._lock = threading.Lock()  # чтобы вывод из разных потоков не перемешивался
        self._total = 0
        self._done = 0
        self._frame_index = 0
        self._label = ""

    def start(self, total: int, label: str = "") -> None:
        with self._lock:
            self._total = total
            self._done = 0
            self._frame_index = 0
            self._label = label
            if total > 0:
                self._render()

    def advance(self, step: int = 1) -> None:
        with self._lock:
            if self._total <= 0:
                return
            self._done = min(self._total, self._done + step)
            self._frame_index = (self._frame_index + 1) % len(self._SPINNER_FRAMES)
            self._render()

    def finish(self) -> None:
        with self._lock:
            if self._total > 0:
                self._stream.write("\n")  # переходим на новую строку, чтобы не затирать прогресс
                self._stream.flush()
            self._total = 0
            self._done = 0

    def _render(self) -> None:
        percent = int(self._done * 100 / self._total)
        spinner = self._SPINNER_FRAMES[self._frame_index]
        label = f"{self._label}: " if self._label else ""
        # \r — возврат каретки в начало строки (без перевода на новую строку).
        line = f"\r{spinner} {label}{percent:3d}% ({self._done}/{self._total})"
        self._stream.write(line)
        self._stream.flush()  # без flush строка может не появиться сразу
