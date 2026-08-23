import tempfile
import unittest
from pathlib import Path

from dup_stat.directory_finder import DirectoryDuplicateFinder, filter_subsumed_file_groups
from dup_stat.finder import DuplicateFinder
from dup_stat.hashing import HashlibFileHasher
from dup_stat.matching import create_strategy
from dup_stat.models import DuplicateGroup, EntryKind, FileRecord
from dup_stat.scanning import RecursiveFileScanner


class DirectoryDuplicateFinderTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _scan(self):
        """Прогоняет обычный файловый поиск (как это делает cli.py) и
        возвращает найденные директории-дубликаты на основе его результата —
        без единого дополнительного чтения файла с диска."""
        finder = DuplicateFinder(
            scanner=RecursiveFileScanner(),
            hasher=HashlibFileHasher(algorithm="sha256"),
            key_strategy=create_strategy("hash"),
        )
        scan_result = finder.find_files(self.root)
        return DirectoryDuplicateFinder().find(self.root, scan_result.sizes, scan_result.hashed_records)

    def test_two_identical_directories_are_found(self):
        dir_a = self.root / "photos_2023"
        dir_b = self.root / "photos_backup"
        (dir_a / "sub").mkdir(parents=True)
        (dir_b / "sub").mkdir(parents=True)

        (dir_a / "a.jpg").write_bytes(b"photo-1" * 100)
        (dir_b / "a.jpg").write_bytes(b"photo-1" * 100)
        (dir_a / "sub" / "b.jpg").write_bytes(b"photo-2" * 100)
        (dir_b / "sub" / "b.jpg").write_bytes(b"photo-2" * 100)

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.kind, EntryKind.DIRECTORY)
        self.assertEqual({r.path for r in group.records}, {dir_a, dir_b})

    def test_directories_with_different_content_are_not_duplicates(self):
        dir_a = self.root / "a"
        dir_b = self.root / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "file.txt").write_bytes(b"content A")
        (dir_b / "file.txt").write_bytes(b"content B")

        groups = self._scan()

        self.assertEqual(groups, [])

    def test_directories_with_different_structure_are_not_duplicates(self):
        dir_a = self.root / "a"
        dir_b = self.root / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "file.txt").write_bytes(b"same content")
        (dir_b / "file.txt").write_bytes(b"same content")
        (dir_b / "extra.txt").write_bytes(b"extra, unique file")

        groups = self._scan()

        self.assertEqual(groups, [])

    def test_directory_with_a_globally_unique_file_is_never_a_duplicate(self):
        # a/unique.txt не имеет "близнеца" нигде в дереве — значит его
        # полный хеш никогда не считается, и директория a не может быть
        # признана дубликатом, даже если остальные файлы совпадают.
        dir_a = self.root / "a"
        dir_b = self.root / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "common.txt").write_bytes(b"shared content")
        (dir_b / "common.txt").write_bytes(b"shared content")
        (dir_a / "unique.txt").write_bytes(b"nothing else like this in the tree")

        groups = self._scan()

        self.assertEqual(groups, [])

    def test_only_top_level_duplicate_directories_are_reported(self):
        # root/parent_a и root/parent_b полностью идентичны, включая
        # вложенную поддиректорию sub — она не должна репортиться
        # отдельной группой, раз уже покрыта родительским дубликатом.
        parent_a = self.root / "parent_a"
        parent_b = self.root / "parent_b"
        (parent_a / "sub").mkdir(parents=True)
        (parent_b / "sub").mkdir(parents=True)
        (parent_a / "sub" / "x.bin").write_bytes(b"nested content")
        (parent_b / "sub" / "x.bin").write_bytes(b"nested content")
        (parent_a / "top.bin").write_bytes(b"top level content")
        (parent_b / "top.bin").write_bytes(b"top level content")

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        reported_paths = {r.path for r in groups[0].records}
        self.assertEqual(reported_paths, {parent_a, parent_b})

    def test_three_identical_directories_form_one_group(self):
        for name in ("copy1", "copy2", "copy3"):
            d = self.root / name
            d.mkdir()
            (d / "f.txt").write_bytes(b"triplicated content")

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].records), 3)

    def test_directory_group_size_is_total_recursive_size(self):
        dir_a = self.root / "a"
        dir_b = self.root / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "one.bin").write_bytes(b"x" * 100)
        (dir_b / "one.bin").write_bytes(b"x" * 100)
        (dir_a / "two.bin").write_bytes(b"y" * 50)
        (dir_b / "two.bin").write_bytes(b"y" * 50)

        groups = self._scan()

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].size_per_copy, 150)

    def test_cli_style_pipeline_hides_files_covered_by_directory_duplicate(self):
        """Воспроизводит то, что делает cli.py: находит файловые и
        директорийные дубликаты, затем убирает файловые группы, целиком
        покрытые директорией — иначе один и тот же дубликат считался бы
        дважды в суммарном размере."""
        dir_a = self.root / "a"
        dir_b = self.root / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "x.bin").write_bytes(b"shared content")
        (dir_b / "x.bin").write_bytes(b"shared content")
        # отдельный файл-дубликат вне директорий — не должен быть скрыт
        (self.root / "note.txt").write_bytes(b"standalone duplicate")
        (self.root / "note_copy.txt").write_bytes(b"standalone duplicate")

        finder = DuplicateFinder(
            scanner=RecursiveFileScanner(),
            hasher=HashlibFileHasher(algorithm="sha256"),
            key_strategy=create_strategy("hash"),
        )
        scan_result = finder.find_files(self.root)
        directory_groups = DirectoryDuplicateFinder().find(
            self.root, scan_result.sizes, scan_result.hashed_records
        )
        file_groups = filter_subsumed_file_groups(scan_result.groups, directory_groups)

        self.assertEqual(len(directory_groups), 1)
        remaining_paths = {r.path for group in file_groups for r in group.records}
        self.assertNotIn(dir_a / "x.bin", remaining_paths)
        self.assertNotIn(dir_b / "x.bin", remaining_paths)
        self.assertIn(self.root / "note.txt", remaining_paths)


class FilterSubsumedFileGroupsTests(unittest.TestCase):
    def _file_group(self, size: int, *paths: str) -> DuplicateGroup:
        records = [
            FileRecord(path=Path(p), name=Path(p).name, size=size, mtime=0.0, file_hash="h")
            for p in paths
        ]
        return DuplicateGroup(key=("h",), records=records)

    def _dir_group(self, *paths: str) -> DuplicateGroup:
        records = [
            FileRecord(path=Path(p), name=Path(p).name, size=100, mtime=0.0, file_hash="sig")
            for p in paths
        ]
        return DuplicateGroup(key=("sig",), records=records, kind=EntryKind.DIRECTORY)

    def test_no_directory_groups_returns_input_unchanged(self):
        file_groups = [self._file_group(10, "a/x.txt", "b/x.txt")]
        self.assertEqual(filter_subsumed_file_groups(file_groups, []), file_groups)

    def test_group_fully_inside_duplicate_directory_is_removed(self):
        file_groups = [self._file_group(10, "root/a/x.txt", "root/b/x.txt")]
        directory_groups = [self._dir_group("root/a", "root/b")]

        self.assertEqual(filter_subsumed_file_groups(file_groups, directory_groups), [])

    def test_group_outside_duplicate_directory_is_kept(self):
        file_groups = [self._file_group(10, "root/note.txt", "root/note_copy.txt")]
        directory_groups = [self._dir_group("root/a", "root/b")]

        self.assertEqual(filter_subsumed_file_groups(file_groups, directory_groups), file_groups)

    def test_group_partially_covered_is_kept(self):
        # Один файл группы внутри дубликат-директории, другой — нет:
        # такая группа не полностью объясняется директорией и остаётся.
        file_groups = [self._file_group(10, "root/a/x.txt", "root/elsewhere/x.txt")]
        directory_groups = [self._dir_group("root/a", "root/b")]

        self.assertEqual(filter_subsumed_file_groups(file_groups, directory_groups), file_groups)


if __name__ == "__main__":
    unittest.main()
