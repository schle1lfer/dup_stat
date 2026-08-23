import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dup_stat.models import DuplicateGroup, FileRecord
from dup_stat.storage import (
    DataFrameResultExporter,
    ResultPersistence,
    SqliteResultExporter,
    make_timestamp,
)

try:
    import pandas as pd

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")


def _sample_groups():
    r1 = FileRecord(path=Path("a.txt"), name="a.txt", size=10, mtime=1.0, file_hash="hash1")
    r2 = FileRecord(path=Path("sub/b.txt"), name="b.txt", size=10, mtime=2.0, file_hash="hash1")
    return [DuplicateGroup(key=("hash1",), records=[r1, r2])]


def _make_group(size: int, file_hash: str) -> DuplicateGroup:
    r1 = FileRecord(path=Path(f"{file_hash}_a"), name=f"{file_hash}_a", size=size, mtime=1.0, file_hash=file_hash)
    r2 = FileRecord(path=Path(f"{file_hash}_b"), name=f"{file_hash}_b", size=size, mtime=2.0, file_hash=file_hash)
    return DuplicateGroup(key=(file_hash,), records=[r1, r2])


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
                    "FROM duplicate_files ORDER BY path"
                ).fetchall()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], "hash1")
            self.assertEqual(rows[0][5], 10)  # wasted_bytes = size * (count - 1)

    def test_export_preserves_given_group_order(self):
        """Экспортёр не переупорядочивает группы — porядок (по размеру,
        по убыванию) обеспечивает DuplicateFinder (см. finder.py)."""
        groups = [_make_group(1000, "big"), _make_group(100, "mid"), _make_group(10, "small")]

        with tempfile.TemporaryDirectory() as tmp:
            db_path = SqliteResultExporter().export(groups, Path(tmp), "20260101_000000")
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT group_id, size_bytes FROM duplicate_files ORDER BY group_id"
                ).fetchall()

            self.assertEqual(rows, [(1, 1000), (2, 100), (3, 10)])


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


class ResultPersistenceTests(unittest.TestCase):
    def test_all_exporters_share_the_same_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            exporters = [SqliteResultExporter()]
            if HAS_PANDAS:
                exporters.append(DataFrameResultExporter())

            paths = ResultPersistence(exporters).save_all(_sample_groups(), out_dir)

            timestamps = {p.stem.rsplit("_", 2)[-2] + "_" + p.stem.rsplit("_", 2)[-1] for p in paths}
            self.assertEqual(len(timestamps), 1, f"Разные timestamp в именах файлов: {paths}")
            self.assertRegex(next(iter(timestamps)), TIMESTAMP_RE)
            for path in paths:
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
