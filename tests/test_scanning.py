import tempfile
import unittest
from pathlib import Path

from dup_stat.scanning import RecursiveFileScanner


class RecursiveFileScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_finds_files_recursively(self):
        (self.root / "sub").mkdir()
        (self.root / "a.txt").write_bytes(b"a")
        (self.root / "sub" / "b.txt").write_bytes(b"bb")

        found = {p.name for p in RecursiveFileScanner().scan(self.root)}

        self.assertEqual(found, {"a.txt", "b.txt"})

    def test_min_size_filters_small_files(self):
        (self.root / "small.txt").write_bytes(b"x")
        (self.root / "big.txt").write_bytes(b"x" * 100)

        found = {p.name for p in RecursiveFileScanner(min_size=10).scan(self.root)}

        self.assertEqual(found, {"big.txt"})

    def test_symlinks_ignored_by_default(self):
        target = self.root / "real.txt"
        target.write_bytes(b"data")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Симлинки не поддерживаются в этом окружении")

        found_default = {p.name for p in RecursiveFileScanner(follow_symlinks=False).scan(self.root)}
        self.assertEqual(found_default, {"real.txt"})

        found_following = {p.name for p in RecursiveFileScanner(follow_symlinks=True).scan(self.root)}
        self.assertEqual(found_following, {"real.txt", "link.txt"})

    def test_raises_for_non_directory(self):
        file_path = self.root / "not_a_dir.txt"
        file_path.write_bytes(b"x")

        with self.assertRaises(NotADirectoryError):
            list(RecursiveFileScanner().scan(file_path))


if __name__ == "__main__":
    unittest.main()
