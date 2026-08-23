import hashlib
import tempfile
import unittest
from pathlib import Path

from dup_stat.hashing import HashlibFileHasher


class HashlibFileHasherTests(unittest.TestCase):
    def test_matches_hashlib_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            content = b"hello world" * 1000
            path.write_bytes(content)

            hasher = HashlibFileHasher(algorithm="sha256")
            expected = hashlib.sha256(content).hexdigest()

            self.assertEqual(hasher.compute(path), expected)

    def test_supports_alternative_algorithm(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            content = b"data"
            path.write_bytes(content)

            hasher = HashlibFileHasher(algorithm="md5")
            self.assertEqual(hasher.compute(path), hashlib.md5(content).hexdigest())

    def test_rejects_unknown_algorithm(self):
        with self.assertRaises(ValueError):
            HashlibFileHasher(algorithm="not-a-real-algorithm")

    def test_max_bytes_hashes_only_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            content = b"0123456789" * 100
            path.write_bytes(content)

            hasher = HashlibFileHasher(algorithm="sha256")
            expected_prefix_hash = hashlib.sha256(content[:16]).hexdigest()

            self.assertEqual(hasher.compute(path, max_bytes=16), expected_prefix_hash)
            self.assertNotEqual(hasher.compute(path, max_bytes=16), hasher.compute(path))

    def test_max_bytes_larger_than_file_equals_full_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            content = b"short"
            path.write_bytes(content)

            hasher = HashlibFileHasher(algorithm="sha256")

            self.assertEqual(hasher.compute(path, max_bytes=10_000), hasher.compute(path))


if __name__ == "__main__":
    unittest.main()
