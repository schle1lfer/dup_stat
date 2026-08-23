import unittest
from pathlib import Path

from dup_stat.matching import AttributeKeyStrategy, create_strategy
from dup_stat.models import FileRecord


def _record(name="a.txt", size=10, mtime=0.0, file_hash="abc"):
    return FileRecord(path=Path(name), name=name, size=size, mtime=mtime, file_hash=file_hash)


class MatchingTests(unittest.TestCase):
    def test_hash_only_strategy_ignores_name(self):
        strategy = create_strategy("hash")
        r1 = _record(name="a.txt", file_hash="same")
        r2 = _record(name="b.txt", file_hash="same")
        self.assertEqual(strategy.key(r1), strategy.key(r2))

    def test_hash_and_name_strategy_distinguishes_names(self):
        strategy = create_strategy("hash+name")
        r1 = _record(name="a.txt", file_hash="same")
        r2 = _record(name="b.txt", file_hash="same")
        self.assertNotEqual(strategy.key(r1), strategy.key(r2))

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            create_strategy("does-not-exist")

    def test_attribute_key_strategy_requires_attributes(self):
        with self.assertRaises(ValueError):
            AttributeKeyStrategy([])

    def test_attribute_key_strategy_rejects_unknown_attribute(self):
        with self.assertRaises(ValueError):
            AttributeKeyStrategy(["does_not_exist"])


if __name__ == "__main__":
    unittest.main()
