"""Мелкие переиспользуемые хелперы (DRY)."""

from collections import defaultdict
from typing import Callable, Dict, Iterable, List, TypeVar

T = TypeVar("T")
K = TypeVar("K")


def human_readable_size(num_bytes: float) -> str:
    """Форматирует размер в байтах в человекочитаемый вид (KB/MB/...)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} EB"


def group_by(items: Iterable[T], key_fn: Callable[[T], K]) -> Dict[K, List[T]]:
    """Группирует элементы по значению key_fn(item), сохраняя порядок внутри группы."""
    groups: Dict[K, List[T]] = defaultdict(list)
    for item in items:
        groups[key_fn(item)].append(item)
    return groups


def drop_singleton_groups(groups: Dict[K, List[T]]) -> List[T]:
    """Разворачивает группы обратно в плоский список, отбрасывая группы из одного элемента.

    Используется на каждой стадии поиска дубликатов (по размеру, по
    частичному хешу): элемент, у которого нет ни одного "соседа" по
    текущему критерию, точно не дубликат, и его незачем анализировать
    дальше — благодаря этому в поиск попадают только реальные кандидаты.
    """
    return [item for group in groups.values() if len(group) > 1 for item in group]
