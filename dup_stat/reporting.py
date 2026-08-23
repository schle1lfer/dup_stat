"""Форматирование результата поиска дубликатов в вывод для пользователя.

ReportFormatter — интерфейс (OCP): чтобы добавить новый формат вывода
(например, CSV), достаточно реализовать новый класс, не трогая CLI
или логику поиска.
"""

from abc import ABC, abstractmethod
import json
from typing import List

from .models import DuplicateGroup
from .utils import human_readable_size


class ReportFormatter(ABC):
    @abstractmethod
    def format(self, groups: List[DuplicateGroup]) -> str:
        raise NotImplementedError


class TextReportFormatter(ReportFormatter):
    """Читаемый человеком текстовый отчёт."""

    def format(self, groups: List[DuplicateGroup]) -> str:
        if not groups:
            return "Дубликаты не найдены."

        ordered = sorted(groups, key=lambda g: g.wasted_size, reverse=True)
        lines: List[str] = []
        total_wasted = 0

        for index, group in enumerate(ordered, start=1):
            total_wasted += group.wasted_size
            sample = group.records[0]
            lines.append(
                f"[{index}] {len(group.records)} файлов, "
                f"размер каждого: {human_readable_size(sample.size)}, "
                f"хеш: {sample.file_hash}"
            )
            for record in group.records:
                lines.append(f"      - {record.path}")

        lines.append("")
        lines.append(f"Найдено групп дубликатов: {len(groups)}")
        lines.append(
            f"Суммарный размер лишних копий: "
            f"{human_readable_size(total_wasted)} ({total_wasted} байт)"
        )
        return "\n".join(lines)


class JsonReportFormatter(ReportFormatter):
    """Машиночитаемый JSON-отчёт."""

    def format(self, groups: List[DuplicateGroup]) -> str:
        total_wasted = sum(g.wasted_size for g in groups)
        payload = {
            "duplicate_groups": [
                {
                    "hash": group.records[0].file_hash,
                    "size_bytes": group.records[0].size,
                    "count": len(group.records),
                    "wasted_bytes": group.wasted_size,
                    "files": [str(r.path) for r in group.records],
                }
                for group in groups
            ],
            "groups_count": len(groups),
            "total_wasted_bytes": total_wasted,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
