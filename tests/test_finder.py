import tempfile
import unittest
from pathlib import Path

from dup_stat.finder import DuplicateFinder
from dup_stat.hash_computation import ThreadPoolHashComputation
from dup_stat.hashing import HashlibFileHasher
from dup_stat.matching import create_strategy
from dup_stat.scanning import RecursiveFileScanner


class DuplicateFinderTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _make_finder(
        self, match: str = "hash", max_workers=None, partial_hash_bytes=None
    ) -> DuplicateFinder:
        kwargs = {}
        if max_workers is not None:
            kwargs["hash_computation"] = ThreadPoolHashComputation(max_workers=max_workers)
        if partial_hash_bytes is not None:
            kwargs["partial_hash_bytes"] = partial_hash_bytes
        return DuplicateFinder(
            scanner=RecursiveFileScanner(),
            hasher=HashlibFileHasher(algorithm="sha256"),
            key_strategy=create_strategy(match),
            **kwargs,
        )

    def test_finds_content_duplicates_regardless_of_name(self):
        sub = self.root / "sub"
        sub.mkdir()
        (self.root / "a.txt").write_bytes(b"same content")
        (sub / "b.txt").write_bytes(b"same content")
        (self.root / "unique.txt").write_bytes(b"unique content")

        groups = self._make_finder("hash").find(self.root)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].records), 2)
        self.assertEqual(groups[0].wasted_size, len(b"same content"))

    def test_no_duplicates_for_distinct_files(self):
        (self.root / "a.txt").write_bytes(b"content A")
        (self.root / "b.txt").write_bytes(b"content B")

        groups = self._make_finder("hash").find(self.root)

        self.assertEqual(groups, [])

    def test_hash_and_name_strategy_requires_matching_name(self):
        sub = self.root / "sub"
        sub.mkdir()
        (self.root / "report.txt").write_bytes(b"same content")
        (sub / "different_name.txt").write_bytes(b"same content")

        groups = self._make_finder("hash+name").find(self.root)

        self.assertEqual(groups, [])

    def test_hash_and_name_strategy_groups_matching_pairs(self):
        sub = self.root / "sub"
        sub.mkdir()
        (self.root / "report.txt").write_bytes(b"same content")
        (sub / "report.txt").write_bytes(b"same content")

        groups = self._make_finder("hash+name").find(self.root)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].records), 2)

    def test_files_with_unique_size_are_never_hashed_as_pairs(self):
        (self.root / "a.txt").write_bytes(b"x")
        (self.root / "b.txt").write_bytes(b"xy")
        (self.root / "c.txt").write_bytes(b"xyz")

        groups = self._make_finder("hash").find(self.root)

        self.assertEqual(groups, [])

    def test_partial_hash_prefilter_still_finds_real_duplicates(self):
        (self.root / "a.txt").write_bytes(b"same content" * 10)
        (self.root / "b.txt").write_bytes(b"same content" * 10)

        groups = self._make_finder("hash", partial_hash_bytes=8).find(self.root)

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].records), 2)

    def test_partial_hash_prefilter_rejects_same_size_different_prefix(self):
        (self.root / "a.txt").write_bytes(b"AAAA" + b"x" * 100)
        (self.root / "b.txt").write_bytes(b"BBBB" + b"x" * 100)

        groups = self._make_finder("hash", partial_hash_bytes=4).find(self.root)

        self.assertEqual(groups, [])

    def test_disabling_partial_hash_prefilter_gives_same_result(self):
        (self.root / "a.txt").write_bytes(b"same content")
        (self.root / "b.txt").write_bytes(b"same content")

        with_prefilter = self._make_finder("hash", partial_hash_bytes=4).find(self.root)
        without_prefilter = self._make_finder("hash", partial_hash_bytes=0).find(self.root)

        self.assertEqual(len(with_prefilter), 1)
        self.assertEqual(len(without_prefilter), 1)
        self.assertEqual(
            {r.path for r in with_prefilter[0].records},
            {r.path for r in without_prefilter[0].records},
        )

    def test_parallel_hash_computation_gives_same_result_as_sequential(self):
        sub = self.root / "sub"
        sub.mkdir()
        (self.root / "a.txt").write_bytes(b"same content")
        (sub / "b.txt").write_bytes(b"same content")
        (self.root / "unique.txt").write_bytes(b"unique content")

        sequential = self._make_finder("hash", max_workers=1).find(self.root)
        parallel = self._make_finder("hash", max_workers=4).find(self.root)

        self.assertEqual(len(sequential), len(parallel))
        seq_paths = {r.path for r in sequential[0].records}
        par_paths = {r.path for r in parallel[0].records}
        self.assertEqual(seq_paths, par_paths)


if __name__ == "__main__":
    unittest.main()
