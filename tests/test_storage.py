import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dup_stat.models import DuplicateGroup, EntryKind, FileRecord
from dup_stat.storage import (
    DataFrameResultExporter,
    ExcelResultExporter,
    JsonResultExporter,
    PersistenceResult,
    ResultExporter,
    ResultPersistence,
    SqliteResultExporter,
    make_timestamp,
)

try:
    import pandas as pd

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import openpyxl  # noqa: F401

    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")


def _sample_groups():
    r1 = FileRecord(path=Path("a.txt"), name="a.txt", size=10, mtime=1.0, file_hash="hash1")
    r2 = FileRecord(path=Path("sub/b.txt"), name="b.txt", size=10, mtime=2.0, file_hash="hash1")
    return [DuplicateGroup(key=("hash1",), records=[r1, r2])]


def _make_group(size: int, file_hash: str) -> DuplicateGroup:
    r1 = FileRecord(path=Path(f"{file_hash}_a"), name=f"{file_hash}_a", size=size, mtime=1.0, file_hash=file_hash)
    r2 = FileRecord(path=Path(f"{file_hash}_b"), name=f"{file_hash}_b", size=size, mtime=2.0, file_hash=file_hash)
    return DuplicateGroup(key=(file_hash,), records=[r1, r2])


class _AlwaysFailingExporter(ResultExporter):
    """Тестовый экспортёр, который всегда падает с ImportError — имитирует
    отсутствующую опциональную зависимость, не трогая настоящий pandas/openpyxl."""

    def export(self, groups, output_dir, timestamp):
        raise ImportError("тестовая зависимость не установлена")


class MakeTimestampTests(unittest.TestCase):
    def test_format(self):
        self.assertRegex(make_timestamp(), TIMESTAMP_RE)


class SqliteResultExporterTests(unittest.TestCase):
    def test_export_writes_expected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            exporter = SqliteResultExporter()
            db_path = exporter.export(_sample_groups(), out_dir, "20260101_000000")

            self.assertEqual(db_path.name, "dup_stat_results_20260101_000000.sqlite3")
            self.assertTrue(db_path.exists())

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT group_id, file_hash, path, name, size_bytes, wasted_bytes "
                    "FROM duplicate_entries ORDER BY path"
                ).fetchall()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], "hash1")
            self.assertEqual(rows[0][5], 10)  # wasted_bytes = size * (count - 1)

    def test_export_includes_kind_column(self):
        dir_r1 = FileRecord(path=Path("dirA"), name="dirA", size=20, mtime=1.0, file_hash="sig1")
        dir_r2 = FileRecord(path=Path("dirB"), name="dirB", size=20, mtime=2.0, file_hash="sig1")
        groups = [
            DuplicateGroup(key=("hash1",), records=_sample_groups()[0].records, kind=EntryKind.FILE),
            DuplicateGroup(key=("sig1",), records=[dir_r1, dir_r2], kind=EntryKind.DIRECTORY),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = SqliteResultExporter().export(groups, Path(tmp), "20260101_000000")
            with sqlite3.connect(db_path) as conn:
                kinds = dict(
                    conn.execute("SELECT DISTINCT path, kind FROM duplicate_entries").fetchall()
                )

            self.assertEqual(kinds["a.txt"], "file")
            self.assertEqual(kinds["dirA"], "directory")

    def test_export_preserves_given_group_order(self):
        """Экспортёр не переупорядочивает группы — porядок (по размеру,
        по убыванию) обеспечивает DuplicateFinder (см. finder.py)."""
        groups = [_make_group(1000, "big"), _make_group(100, "mid"), _make_group(10, "small")]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = SqliteResultExporter().export(groups, Path(tmp), "20260101_000000")
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT group_id, size_bytes FROM duplicate_entries ORDER BY group_id"
                ).fetchall()

            self.assertEqual(rows, [(1, 1000), (2, 100), (3, 10)])


class JsonResultExporterTests(unittest.TestCase):
    def test_export_writes_expected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            json_path = JsonResultExporter().export(_sample_groups(), out_dir, "20260101_000000")

            self.assertEqual(json_path.name, "dup_stat_results_20260101_000000.json")
            with json_path.open("r", encoding="utf-8") as f:
                rows = json.load(f)

            self.assertEqual(len(rows), 2)
            self.assertEqual({r["file_hash"] for r in rows}, {"hash1"})
            self.assertEqual({r["kind"] for r in rows}, {"file"})

    def test_export_does_not_require_pandas(self):
        # Не проверяем отсутствие pandas напрямую (он может быть
        # установлен в тестовом окружении) — просто убеждаемся, что
        # JsonResultExporter работает и без импорта pandas внутри себя.
        with tempfile.TemporaryDirectory() as tmp:
            json_path = JsonResultExporter().export(_sample_groups(), Path(tmp), "20260101_000000")
            self.assertTrue(json_path.exists())


@unittest.skipUnless(HAS_PANDAS, "pandas не установлен")
class DataFrameResultExporterTests(unittest.TestCase):
    def test_export_writes_loadable_dataframe(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            exporter = DataFrameResultExporter()
            pkl_path = exporter.export(_sample_groups(), out_dir, "20260101_000000")

            self.assertEqual(pkl_path.name, "dup_stat_results_20260101_000000.pkl")
            df = pd.read_pickle(pkl_path)

            self.assertEqual(len(df), 2)
            self.assertIn("file_hash", df.columns)
            self.assertEqual(set(df["file_hash"]), {"hash1"})


@unittest.skipUnless(HAS_PANDAS and HAS_OPENPYXL, "pandas и/или openpyxl не установлены")
class ExcelResultExporterTests(unittest.TestCase):
    def test_export_writes_loadable_excel(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            exporter = ExcelResultExporter()
            xlsx_path = exporter.export(_sample_groups(), out_dir, "20260101_000000")

            self.assertEqual(xlsx_path.name, "dup_stat_results_20260101_000000.xlsx")
            df = pd.read_excel(xlsx_path)

            self.assertEqual(len(df), 2)
            self.assertIn("file_hash", df.columns)
            self.assertEqual(set(df["file_hash"]), {"hash1"})


class ResultPersistenceTests(unittest.TestCase):
    def test_all_exporters_share_the_same_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            exporters = [SqliteResultExporter(), JsonResultExporter()]
            if HAS_PANDAS:
                exporters.append(DataFrameResultExporter())
            if HAS_PANDAS and HAS_OPENPYXL:
                exporters.append(ExcelResultExporter())

            result = ResultPersistence(exporters).save_all(_sample_groups(), out_dir)

            self.assertIsInstance(result, PersistenceResult)
            self.assertEqual(result.skipped, [])
            paths = result.saved
            timestamps = {p.stem.rsplit("_", 2)[-2] + "_" + p.stem.rsplit("_", 2)[-1] for p in paths}
            self.assertEqual(len(timestamps), 1, f"Разные timestamp в именах файлов: {paths}")
            self.assertRegex(next(iter(timestamps)), TIMESTAMP_RE)
            for path in paths:
                self.assertTrue(path.exists())

    def test_previous_timestamp_is_prepended_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result = ResultPersistence([SqliteResultExporter()]).save_all(
                _sample_groups(), out_dir, previous_timestamp="20260101_000000"
            )

            self.assertEqual(len(result.saved), 1)
            name = result.saved[0].name
            self.assertTrue(
                name.startswith("dup_stat_results_20260101_000000_"),
                f"Ожидали цепочку timestamp'ов в имени, получили: {name}",
            )
            # После префикса должен идти ЕЩЁ ОДИН свежий timestamp, а не пустота.
            prefix_len = len("dup_stat_results_20260101_000000_")
            new_part = name[prefix_len:-len(".sqlite3")]
            self.assertRegex(new_part, TIMESTAMP_RE)

    def test_chaining_can_be_applied_repeatedly(self):
        """Имитирует несколько последовательных --append-timestamp:
        цепочка timestamp'ов в имени должна расти с каждым разом."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            persistence = ResultPersistence([JsonResultExporter()])

            first = persistence.save_all(_sample_groups(), out_dir).saved[0]
            first_ts = first.stem[len("dup_stat_results_"):]

            second = persistence.save_all(_sample_groups(), out_dir, previous_timestamp=first_ts).saved[0]
            second_ts = second.stem[len("dup_stat_results_"):]
            self.assertTrue(second_ts.startswith(first_ts + "_"))

            third = persistence.save_all(_sample_groups(), out_dir, previous_timestamp=second_ts).saved[0]
            third_ts = third.stem[len("dup_stat_results_"):]
            self.assertTrue(third_ts.startswith(second_ts + "_"))

            # Все три файла — разные, ни один не перезаписан.
            self.assertEqual(len({first, second, third}), 3)
            for path in (first, second, third):
                self.assertTrue(path.exists())

    def test_failing_exporter_is_skipped_without_blocking_others(self):
        """Один экспортёр без нужной зависимости не должен мешать
        сохранить остальные форматы."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            exporters = [SqliteResultExporter(), _AlwaysFailingExporter(), JsonResultExporter()]

            result = ResultPersistence(exporters).save_all(_sample_groups(), out_dir)

            self.assertEqual(len(result.saved), 2)  # sqlite и json сохранились
            self.assertEqual(len(result.skipped), 1)
            self.assertIn("тестовая зависимость не установлена", result.skipped[0])
            suffixes = {p.suffix for p in result.saved}
            self.assertEqual(suffixes, {".sqlite3", ".json"})


if __name__ == "__main__":
    unittest.main()
