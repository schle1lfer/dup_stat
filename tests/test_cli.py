import contextlib
import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from dup_stat.cli import main

TIMESTAMP_RE = re.compile(r"dup_stat_results_(\d{8}_\d{6})\.sqlite3")


class AppendTimestampTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.scan_dir = self.root / "scan"
        self.save_dir = self.root / "results"
        self.scan_dir.mkdir()

    def tearDown(self):
        self.tmp_dir.cleanup()

    @staticmethod
    def _run(args):
        """Запускает CLI как обычно вызвал бы пользователь, перехватывая
        stdout/stderr, чтобы проверить их содержимое в тесте."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(args)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def _extract_timestamp(self, stderr_text):
        match = TIMESTAMP_RE.search(stderr_text)
        self.assertIsNotNone(match, f"Не нашли timestamp в выводе:\n{stderr_text}")
        return match.group(1)

    def test_append_timestamp_merges_and_keeps_old_files(self):
        (self.scan_dir / "a.txt").write_bytes(b"first duplicate pair")
        (self.scan_dir / "b.txt").write_bytes(b"first duplicate pair")

        exit_code, _, stderr1 = self._run(
            [str(self.scan_dir), "--save-dir", str(self.save_dir), "--no-progress"]
        )
        self.assertEqual(exit_code, 0)
        first_ts = self._extract_timestamp(stderr1)
        old_files = sorted(p.name for p in self.save_dir.glob(f"*{first_ts}*"))
        # sqlite3 + json гарантированы всегда; pkl/xlsx — если есть pandas/openpyxl.
        self.assertGreaterEqual(len(old_files), 2)

        # Гарантируем другой timestamp у второго запуска (разрешение — секунды).
        time.sleep(1.1)

        # Второй прогон: новая пара дубликатов в той же директории.
        (self.scan_dir / "c.txt").write_bytes(b"second duplicate pair")
        (self.scan_dir / "d.txt").write_bytes(b"second duplicate pair")

        exit_code, _, stderr2 = self._run(
            [
                str(self.scan_dir),
                "--save-dir",
                str(self.save_dir),
                "--append-timestamp",
                first_ts,
                "--no-progress",
            ]
        )
        self.assertEqual(exit_code, 0)
        second_ts = self._extract_timestamp(stderr2)
        self.assertNotEqual(first_ts, second_ts)

        # Старые файлы результата не должны были измениться или исчезнуть.
        for name in old_files:
            self.assertTrue((self.save_dir / name).exists(), f"Пропал старый файл: {name}")

        # Новый набор файлов появился рядом со старым, под новым timestamp.
        new_files = sorted(p.name for p in self.save_dir.glob(f"*{second_ts}*"))
        self.assertEqual(len(new_files), len(old_files))

        # В объединённом JSON должны быть обе пары дубликатов.
        merged_json_path = self.save_dir / f"dup_stat_results_{second_ts}.json"
        rows = json.loads(merged_json_path.read_text(encoding="utf-8"))
        paths = {Path(r["path"]).name for r in rows}
        self.assertEqual(paths, {"a.txt", "b.txt", "c.txt", "d.txt"})

    def test_append_timestamp_without_save_dir_is_an_error(self):
        exit_code, _, stderr = self._run(
            [str(self.scan_dir), "--append-timestamp", "20260101_000000", "--no-progress"]
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("--save-dir", stderr)

    def test_append_timestamp_with_unknown_timestamp_is_an_error(self):
        exit_code, _, stderr = self._run(
            [
                str(self.scan_dir),
                "--save-dir",
                str(self.save_dir),
                "--append-timestamp",
                "19700101_000000",
                "--no-progress",
            ]
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("Не найден файл предыдущих результатов", stderr)


if __name__ == "__main__":
    unittest.main()
