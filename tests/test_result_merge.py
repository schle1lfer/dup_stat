import json
import tempfile
import unittest
from pathlib import Path

from dup_stat.models import DuplicateGroup, EntryKind, FileRecord
from dup_stat.result_merge import load_previous_rows, merge_with_previous


def _row(path, name, size, mtime, file_hash, kind="file", group_id=1):
    return {
        "group_id": group_id,
        "kind": kind,
        "file_hash": file_hash,
        "path": path,
        "name": name,
        "size_bytes": size,
        "mtime": mtime,
        "wasted_bytes": size,
    }


def _group(records, kind=EntryKind.FILE):
    return DuplicateGroup(key=(records[0].file_hash,), records=list(records), kind=kind)


def _record(path, size=10, mtime=1.0, file_hash="h"):
    return FileRecord(path=Path(path), name=Path(path).name, size=size, mtime=mtime, file_hash=file_hash)


class LoadPreviousRowsTests(unittest.TestCase):
    def test_reads_saved_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_dir = Path(tmp)
            json_path = save_dir / "dup_stat_results_20260101_000000.json"
            rows = [_row("a.txt", "a.txt", 10, 1.0, "hash1")]
            json_path.write_text(json.dumps(rows), encoding="utf-8")

            loaded = load_previous_rows(save_dir, "20260101_000000")

            self.assertEqual(loaded, rows)

    def test_raises_for_missing_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_previous_rows(Path(tmp), "20260101_000000")


class MergeWithPreviousTests(unittest.TestCase):
    def test_disjoint_old_and_new_groups_are_both_kept(self):
        old_rows = [
            _row("old_a.txt", "old_a.txt", 10, 1.0, "old_hash"),
            _row("old_b.txt", "old_b.txt", 10, 1.0, "old_hash"),
        ]
        new_groups = [_group([_record("new_a.txt", file_hash="new_hash"), _record("new_b.txt", file_hash="new_hash")])]

        merged = merge_with_previous(new_groups, old_rows)

        hashes = {g.records[0].file_hash for g in merged}
        self.assertEqual(hashes, {"old_hash", "new_hash"})

    def test_new_scan_adds_a_third_copy_to_an_existing_group(self):
        old_rows = [
            _row("a.txt", "a.txt", 10, 1.0, "same_hash"),
            _row("b.txt", "b.txt", 10, 1.0, "same_hash"),
        ]
        # Новый прогон нашёл третий файл с тем же содержимым.
        new_groups = [_group([_record("a.txt", file_hash="same_hash"), _record("c.txt", file_hash="same_hash")])]

        merged = merge_with_previous(new_groups, old_rows)

        self.assertEqual(len(merged), 1)
        paths = {str(r.path) for r in merged[0].records}
        self.assertEqual(paths, {"a.txt", "b.txt", "c.txt"})

    def test_rescanned_path_keeps_fresh_record_not_stale_one(self):
        old_rows = [
            _row("a.txt", "a.txt", 10, 1.0, "same_hash"),
            _row("b.txt", "b.txt", 10, 1.0, "same_hash"),
        ]
        fresh_record = _record("a.txt", size=10, mtime=999.0, file_hash="same_hash")
        new_groups = [_group([fresh_record, _record("b.txt", size=10, mtime=1.0, file_hash="same_hash")])]

        merged = merge_with_previous(new_groups, old_rows)

        self.assertEqual(len(merged), 1)
        a_record = next(r for r in merged[0].records if str(r.path) == "a.txt")
        self.assertEqual(a_record.mtime, 999.0)  # свежая запись, а не устаревшая (mtime=1.0)

    def test_old_only_group_survives_when_new_scan_is_unrelated(self):
        old_rows = [
            _row("old_a.txt", "old_a.txt", 10, 1.0, "old_hash"),
            _row("old_b.txt", "old_b.txt", 10, 1.0, "old_hash"),
        ]

        merged = merge_with_previous([], old_rows)  # новый прогон вообще ничего не нашёл

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].records[0].file_hash, "old_hash")

    def test_result_is_sorted_by_size_descending(self):
        old_rows = [
            _row("small_a.txt", "small_a.txt", 10, 1.0, "small_hash"),
            _row("small_b.txt", "small_b.txt", 10, 1.0, "small_hash"),
        ]
        new_groups = [
            _group([_record("big_a.bin", size=1000, file_hash="big_hash"), _record("big_b.bin", size=1000, file_hash="big_hash")])
        ]

        merged = merge_with_previous(new_groups, old_rows)

        sizes = [g.size_per_copy for g in merged]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_directory_and_file_groups_with_same_hash_stay_separate(self):
        old_rows = [_row("dirA", "dirA", 100, 1.0, "shared_hash", kind="directory")]
        new_groups = [
            _group(
                [_record("file_a.bin", size=100, file_hash="shared_hash"), _record("file_b.bin", size=100, file_hash="shared_hash")],
                kind=EntryKind.FILE,
            )
        ]

        merged = merge_with_previous(new_groups, old_rows)

        # Директория с этим хешем осталась одиночной записью (не дубликат сама по себе)
        # и не смешалась с файловой группой того же хеша.
        kinds = {g.kind for g in merged}
        self.assertEqual(kinds, {EntryKind.FILE})
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].records), 2)


if __name__ == "__main__":
    unittest.main()
