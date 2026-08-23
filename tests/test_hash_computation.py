import tempfile
import unittest
from pathlib import Path

from dup_stat.hash_computation import ThreadPoolHashComputation
from dup_stat.hashing import HashlibFileHasher


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


if __name__ == "__main__":
    unittest.main()
