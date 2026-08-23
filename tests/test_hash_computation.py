import tempfile
import threading
import unittest
from pathlib import Path

from dup_stat.hash_computation import ThreadPoolHashComputation
from dup_stat.hashing import HashlibFileHasher
from dup_stat.progress import ProgressReporter


class _RecordingProgressReporter(ProgressReporter):
    """Тестовый ProgressReporter, который просто запоминает вызовы —
    чтобы проверить, что ThreadPoolHashComputation действительно сообщает
    о прогрессе, не завязываясь на реальный вывод в терминал."""

    def __init__(self):
        self.starts = []  # список (total, label)
        self.advance_count = 0
        self.finish_count = 0
        self._lock = threading.Lock()  # advance() может звать несколько потоков сразу

    def start(self, total, label=""):
        self.starts.append((total, label))

    def advance(self, step=1):
        with self._lock:
            self.advance_count += step

    def finish(self):
        self.finish_count += 1


class ThreadPoolHashComputationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.hasher = HashlibFileHasher(algorithm="sha256")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _make_files(self, count: int):
        paths = []
        for i in range(count):
            path = self.root / f"file_{i}.txt"
            path.write_bytes(f"content-{i}".encode())
            paths.append(path)
        return paths

    def test_sequential_and_parallel_produce_same_hashes(self):
        paths = self._make_files(5)

        sequential = ThreadPoolHashComputation(max_workers=1).compute_many(paths, self.hasher)
        parallel = ThreadPoolHashComputation(max_workers=4).compute_many(paths, self.hasher)

        self.assertEqual(sequential, parallel)
        self.assertEqual(set(sequential.keys()), set(paths))

    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(ThreadPoolHashComputation().compute_many([], self.hasher), {})

    def test_missing_file_is_skipped_not_raised(self):
        missing = self.root / "does_not_exist.txt"

        result = ThreadPoolHashComputation(max_workers=1).compute_many([missing], self.hasher)

        self.assertEqual(result, {})

    def test_missing_file_is_skipped_in_parallel_mode(self):
        missing = self.root / "does_not_exist.txt"
        existing = self._make_files(1)[0]

        result = ThreadPoolHashComputation(max_workers=4).compute_many(
            [missing, existing], self.hasher
        )

        self.assertEqual(set(result.keys()), {existing})

    def test_rejects_non_positive_max_workers(self):
        with self.assertRaises(ValueError):
            ThreadPoolHashComputation(max_workers=0)

    def test_progress_is_reported_in_sequential_mode(self):
        paths = self._make_files(3)
        progress = _RecordingProgressReporter()

        ThreadPoolHashComputation(max_workers=1, progress=progress).compute_many(
            paths, self.hasher, label="полный хеш"
        )

        self.assertEqual(progress.starts, [(3, "полный хеш")])
        self.assertEqual(progress.advance_count, 3)
        self.assertEqual(progress.finish_count, 1)

    def test_progress_is_reported_in_parallel_mode(self):
        paths = self._make_files(5)
        progress = _RecordingProgressReporter()

        ThreadPoolHashComputation(max_workers=4, progress=progress).compute_many(
            paths, self.hasher, label="предварительный хеш"
        )

        self.assertEqual(progress.starts, [(5, "предварительный хеш")])
        self.assertEqual(progress.advance_count, 5)
        self.assertEqual(progress.finish_count, 1)

    def test_progress_not_started_for_empty_input(self):
        progress = _RecordingProgressReporter()

        ThreadPoolHashComputation(progress=progress).compute_many([], self.hasher)

        self.assertEqual(progress.starts, [])
        self.assertEqual(progress.finish_count, 0)

    def test_progress_advances_even_for_missing_files(self):
        missing = self.root / "does_not_exist.txt"
        progress = _RecordingProgressReporter()

        ThreadPoolHashComputation(max_workers=1, progress=progress).compute_many(
            [missing], self.hasher
        )

        # Файл не удалось прочитать, но попытка всё равно засчитывается в прогресс.
        self.assertEqual(progress.advance_count, 1)


if __name__ == "__main__":
    unittest.main()
