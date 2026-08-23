import json
import unittest
from pathlib import Path

from dup_stat.models import DuplicateGroup, FileRecord
from dup_stat.reporting import JsonReportFormatter, TextReportFormatter


def _group(size: int, count: int = 2) -> DuplicateGroup:
    records = [
        FileRecord(
            path=Path(f"file_{size}_{i}.bin"),
            name=f"file_{size}_{i}.bin",
            size=size,
            mtime=0.0,
            file_hash=f"hash-{size}",
        )
        for i in range(count)
    ]
    return DuplicateGroup(key=(f"hash-{size}",), records=records)


class ReportFormatterOrderingTests(unittest.TestCase):
    """Форматтеры не должны переупорядочивать группы — сортировка по
    размеру (по убыванию) — обязанность DuplicateFinder (см. finder.py)."""

    def setUp(self):
        # Уже отсортировано по убыванию размера, как это делает DuplicateFinder.
        self.groups = [_group(1000), _group(100), _group(10)]

    def test_text_formatter_preserves_given_order(self):
        text = TextReportFormatter().format(self.groups)
        positions = [text.index(f"file_{size}_0") for size in (1000, 100, 10)]
        self.assertEqual(positions, sorted(positions))

    def test_json_formatter_preserves_given_order(self):
        payload = json.loads(JsonReportFormatter().format(self.groups))
        sizes = [g["size_bytes"] for g in payload["duplicate_groups"]]
        self.assertEqual(sizes, [1000, 100, 10])

    def test_reversed_input_is_not_resorted(self):
        # Форматтер не должен сам сортировать — если ему передать
        # неотсортированные группы, он должен вывести их как есть.
        reversed_groups = list(reversed(self.groups))
        payload = json.loads(JsonReportFormatter().format(reversed_groups))
        sizes = [g["size_bytes"] for g in payload["duplicate_groups"]]
        self.assertEqual(sizes, [10, 100, 1000])


if __name__ == "__main__":
    unittest.main()
