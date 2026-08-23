"""Стратегии определения того, что считать 'дубликатом'.

По умолчанию дубликатами считаются файлы с одинаковым содержимым (хешем).
Но задача явно допускает уточнение критерия другими атрибутами файла —
именем, размером и т.д. Это реализовано через паттерн Strategy: одна
универсальная реализация AttributeKeyStrategy сравнивает набор атрибутов
FileRecord, а конкретный набор атрибутов задаётся пресетом (DRY — не
плодим по классу на каждую комбинацию атрибутов).
"""

from abc import ABC, abstractmethod
from typing import Dict, Sequence, Tuple

from .models import FileRecord


class DuplicateKeyStrategy(ABC):
    """Вычисляет ключ группировки для записи о файле.

    Файлы с одинаковым ключом считаются дубликатами друг друга.
    Ключ — это кортеж (tuple) значений: например, для критерия "hash"
    это будет просто (file_hash,), а для "hash+name" — (name, file_hash).
    Два файла — дубликаты, если их ключи полностью совпадают.
    """

    @abstractmethod
    def key(self, record: FileRecord) -> Tuple:
        raise NotImplementedError

    @abstractmethod
    def description(self) -> str:
        """Человекочитаемое описание критерия — для отчётов/логов."""
        raise NotImplementedError


class AttributeKeyStrategy(DuplicateKeyStrategy):
    """Строит ключ из произвольного набора атрибутов FileRecord."""

    def __init__(self, attributes: Sequence[str]):
        if not attributes:
            raise ValueError("Нужен хотя бы один атрибут для сравнения")
        # Проверяем, что каждое переданное имя атрибута реально
        # существует у FileRecord (например, "size", "name", "file_hash") —
        # чтобы опечатка обнаружилась сразу, а не посреди сканирования.
        for attr in attributes:
            if not hasattr(FileRecord, "__dataclass_fields__") or attr not in FileRecord.__dataclass_fields__:
                raise ValueError(f"FileRecord не содержит атрибута {attr!r}")
        self._attributes = tuple(attributes)

    def key(self, record: FileRecord) -> Tuple:
        # getattr(record, "size") равносильно record.size, но имя атрибута
        # здесь — переменная, поэтому нельзя написать record.attr напрямую.
        return tuple(getattr(record, attr) for attr in self._attributes)

    def description(self) -> str:
        return " + ".join(self._attributes)


# Именованные пресеты критериев совпадения. file_hash всегда участвует —
# это единственный способ достоверно сравнить содержимое файлов.
MATCH_PRESETS: Dict[str, Tuple[str, ...]] = {
    "hash": ("file_hash",),
    "hash+name": ("name", "file_hash"),
    "hash+name+size": ("name", "size", "file_hash"),
}


def create_strategy(preset: str) -> DuplicateKeyStrategy:
    """Фабрика стратегий по имени пресета (см. MATCH_PRESETS).

    "Фабрика" — функция, которая по простому названию (строке)
    собирает и возвращает готовый объект нужного класса.
    """
    try:
        attributes = MATCH_PRESETS[preset]
    except KeyError as exc:
        available = ", ".join(sorted(MATCH_PRESETS))
        raise ValueError(f"Неизвестный критерий {preset!r}. Доступны: {available}") from exc
    return AttributeKeyStrategy(attributes)
