import tempfile
import unittest
from pathlib import Path

from dup_stat.finder import DuplicateFinder
from dup_stat.hashing import HashlibFileHasher
from dup_stat.matching import create_strategy
from dup_stat.scanning import RecursiveFileScanner


class DuplicateFinderTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _make_finder(self, match: str = "hash") -> DuplicateFinder:
        return DuplicateFinder(
            scanner=RecursiveFileScanner(),
            hasher=HashlibFileHasher(algorithm="sha256"),
            key_strategy=create_strategy(match),
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


if __name__ == "__main__":
    unittest.main()
