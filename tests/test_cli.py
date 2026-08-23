import contextlib
import io
import json
import re
import tempfile
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

    def _saved_sqlite_name(self, stderr_text):
        """Достаёт имя сохранённого .sqlite3 из строки 'Сохранено: /path/to/file.sqlite3'."""
        match = re.search(r"Сохранено: .*/(dup_stat_results_[^/]+\.sqlite3)", stderr_text)
        self.assertIsNotNone(match, f"Не нашли имя сохранённого файла в выводе:\n{stderr_text}")
        return match.group(1)

    def test_append_timestamp_chains_instead_of_replacing(self):
        """Ровно сценарий из описания: dup_stat_results_<ts1> -> после
        --append-timestamp ts1 получаем dup_stat_results_<ts1>_<ts2>,
        старый файл при этом остаётся на месте."""
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

        # Старые файлы результата не должны были измениться или исчезнуть.
        for name in old_files:
            self.assertTrue((self.save_dir / name).exists(), f"Пропал старый файл: {name}")

        # Новый sqlite3 назван 'dup_stat_results_<first_ts>_<новый timestamp>.sqlite3' —
        # старый timestamp в начале имени, новый дописан следом, а не заменил его.
        new_sqlite_name = self._saved_sqlite_name(stderr2)
        self.assertTrue(
            new_sqlite_name.startswith(f"dup_stat_results_{first_ts}_"),
            f"Ожидали, что новое имя начинается с 'dup_stat_results_{first_ts}_', получили: {new_sqlite_name}",
        )
        self.assertNotIn(new_sqlite_name, old_files)  # это точно новый файл, не переиспользованное имя

        # Полная цепочка timestamp'ов из имени нового файла (без префикса/расширения).
        chained_ts = new_sqlite_name[len("dup_stat_results_") : -len(".sqlite3")]
        self.assertNotEqual(chained_ts, first_ts)

        # В объединённом JSON (под тем же составным именем) — обе пары дубликатов.
        merged_json_path = self.save_dir / f"dup_stat_results_{chained_ts}.json"
        self.assertTrue(merged_json_path.exists())
        rows = json.loads(merged_json_path.read_text(encoding="utf-8"))
        paths = {Path(r["path"]).name for r in rows}
        self.assertEqual(paths, {"a.txt", "b.txt", "c.txt", "d.txt"})

    def test_third_run_extends_the_chain_to_three_timestamps(self):
        (self.scan_dir / "a.txt").write_bytes(b"pair one")
        (self.scan_dir / "b.txt").write_bytes(b"pair one")
        _, _, stderr1 = self._run([str(self.scan_dir), "--save-dir", str(self.save_dir), "--no-progress"])
        first_ts = self._extract_timestamp(stderr1)

        (self.scan_dir / "c.txt").write_bytes(b"pair two")
        (self.scan_dir / "d.txt").write_bytes(b"pair two")
        _, _, stderr2 = self._run(
            [str(self.scan_dir), "--save-dir", str(self.save_dir), "--append-timestamp", first_ts, "--no-progress"]
        )
        second_chained_name = self._saved_sqlite_name(stderr2)
        second_ts = second_chained_name[len("dup_stat_results_") : -len(".sqlite3")]

        # Третья новая директория для поиска — как в описанном сценарии.
        third_scan_dir = self.root / "scan2"
        third_scan_dir.mkdir()
        (third_scan_dir / "e.txt").write_bytes(b"pair three")
        (third_scan_dir / "f.txt").write_bytes(b"pair three")
        _, _, stderr3 = self._run(
            [str(third_scan_dir), "--save-dir", str(self.save_dir), "--append-timestamp", second_ts, "--no-progress"]
        )
        third_chained_name = self._saved_sqlite_name(stderr3)
        third_ts = third_chained_name[len("dup_stat_results_") : -len(".sqlite3")]

        # Цепочка растёт: третье имя начинается со всей второй цепочки.
        self.assertTrue(third_ts.startswith(second_ts + "_"))
        # В имени файла теперь ровно три "сегмента" timestamp'а (день_время x3).
        self.assertEqual(len(re.findall(r"\d{8}_\d{6}", third_ts)), 3)

        # Все три прогона видны в объединённых данных одновременно.
        merged_json_path = self.save_dir / f"dup_stat_results_{third_ts}.json"
        rows = json.loads(merged_json_path.read_text(encoding="utf-8"))
        names = {Path(r["path"]).name for r in rows}
        self.assertEqual(names, {"a.txt", "b.txt", "c.txt", "d.txt", "e.txt", "f.txt"})

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
