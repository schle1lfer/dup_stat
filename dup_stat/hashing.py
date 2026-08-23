"""Вычисление хешсумм файлов.

Хеш (хешсумма) — это короткая "цифровая подпись" содержимого файла.
Если у двух файлов совпадает хеш — с огромной вероятностью у них
одинаковое содержимое (это и есть способ находить дубликаты).

Абстракция FileHasher позволяет подменить алгоритм хеширования (OCP) —
например, добавить более быстрый некриптографический хеш, не трогая
остальной код.

compute() умеет считать хеш не только всего файла, но и его префикса
(max_bytes) — один и тот же метод используется и для быстрого
предварительного хеша (см. finder.py), и для полного, вместо двух
почти одинаковых реализаций (DRY).
"""

from abc import ABC, abstractmethod  # инструменты для описания "интерфейса" — abstract base class
import hashlib  # стандартный модуль Python для хеширования (sha256, md5 и т.д.)
from pathlib import Path
from typing import Optional  # Optional[int] значит "int или None"

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 МБ — не грузим весь файл в память


class FileHasher(ABC):
    """Интерфейс вычислителя хешсуммы файла.

    Это "контракт": любой класс-наследник обязан реализовать метод
    compute(). Сам FileHasher создать напрямую нельзя — это только шаблон.
    """

    @abstractmethod  # помечает метод как обязательный для переопределения в наследниках
    def compute(self, path: Path, max_bytes: Optional[int] = None) -> str:
        """Возвращает хеш содержимого файла в виде hex-строки.

        Если max_bytes задан, хешируются только первые max_bytes байт
        файла (используется для быстрого предварительного отсева).
        """
        raise NotImplementedError


class HashlibFileHasher(FileHasher):
    """Хешер поверх стандартного hashlib, читает файл потоково блоками.

    "Потоково блоками" — значит файл читается небольшими кусками
    (chunk_size байт за раз), а не целиком в память. Так можно хешировать
    файлы любого размера, даже очень большие.
    """

    def __init__(self, algorithm: str = "sha256", chunk_size: int = DEFAULT_CHUNK_SIZE):
        # Проверяем аргументы сразу при создании объекта — чтобы ошибка
        # всплыла сразу, а не посреди долгого сканирования директории.
        if algorithm not in hashlib.algorithms_available:
            raise ValueError(f"Неподдерживаемый алгоритм хеширования: {algorithm!r}")
        if chunk_size <= 0:
            raise ValueError("chunk_size должен быть положительным")
        # Имена атрибутов с подчёркиванием (_algorithm) — по соглашению
        # означают "приватное", то есть не предназначено для использования
        # снаружи класса напрямую.
        self._algorithm = algorithm
        self._chunk_size = chunk_size

    @property  # позволяет писать hasher.algorithm вместо hasher.algorithm()
    def algorithm(self) -> str:
        return self._algorithm

    def compute(self, path: Path, max_bytes: Optional[int] = None) -> str:
        hasher = hashlib.new(self._algorithm)  # создаём "накопитель" хеша для выбранного алгоритма
        remaining = max_bytes  # сколько байт ещё осталось прочитать (None = без ограничения)

        with path.open("rb") as f:  # "rb" — открыть файл в бинарном режиме для чтения
            while remaining is None or remaining > 0:
                # Сколько байт читать за один раз: не больше chunk_size,
                # и не больше того, что осталось (если задан max_bytes).
                read_size = self._chunk_size if remaining is None else min(self._chunk_size, remaining)
                chunk = f.read(read_size)
                if not chunk:  # пустой результат — файл закончился
                    break
                hasher.update(chunk)  # добавляем очередной кусок в хеш
                if remaining is not None:
                    remaining -= len(chunk)

        return hasher.hexdigest()  # итоговый хеш в виде строки из 16-ричных цифр
