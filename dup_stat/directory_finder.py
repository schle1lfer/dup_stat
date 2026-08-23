"""Поиск директорий-дубликатов.

Директория считается полным дубликатом другой, если у них совпадает
вся рекурсивная структура (имена и вложенность файлов и поддиректорий)
и содержимое каждого файла — то есть можно удалить одну из директорий
целиком и ничего не потерять.

Ключевая идея, которая делает это бесплатным по I/O: DirectoryDuplicateFinder
не читает с диска ни одного байта сам, а полностью переиспользует то,
что уже посчитал DuplicateFinder (FileScanResult — все размеры файлов
и полные хеши тех из них, что прошли файловый пайплайн). Это корректно
благодаря простому факту:

    Любой файл, действительно входящий в пару директорий-дубликатов,
    обязан иметь где-то в дереве точный "близнец" по содержимому (в
    парной директории) — а значит, у него как минимум два файла с
    одинаковым размером, и он гарантированно проходит все три стадии
    файлового пайплайна (size -> partial hash -> full hash) и уже
    хеширован. Файл без "близнеца" (глобально уникальный по размеру)
    не может быть частью дубликата-директории в принципе — а значит,
    и содержащая его директория, и все её родители тоже не могут иметь
    дубликат. Такие поддеревья отсекаются без построения сигнатуры.

Директория-сигнатура строится снизу вверх (как в Merkle-дереве): хеш
директории — это хеш отсортированного списка (имя, тип, хеш) всех её
прямых потомков, где для поддиректорий используется уже посчитанная
сигнатура. Совпадающая сигнатура двух директорий гарантирует побайтовое
совпадение всего содержимого рекурсивно.

Чтобы не заваливать отчёт дублирующей информацией, репортятся только
"верхние" пары дубликатов — если найдена пара директорий-дубликатов, их
вложенные поддиректории (тоже технически совпадающие) не показываются
отдельно. По той же причине filter_subsumed_file_groups() убирает из
файловых групп те, что целиком лежат внутри уже найденной пары
директорий-дубликатов — иначе один и тот же физический дубликат
считался бы дважды: и как часть директории, и как отдельный файл, что
завышало бы суммарный размер "лишних" копий в отчёте.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .models import DuplicateGroup, EntryKind, FileRecord, sort_groups_by_size_desc
from .utils import group_by


@dataclass
class _DirNode:
    path: Path
    reproducible: bool
    signature: Optional[str]
    total_size: int


class DirectoryDuplicateFinder:
    def find(
        self,
        root: Path,
        sizes: Dict[Path, int],
        hashed_records: List[FileRecord],
    ) -> List[DuplicateGroup]:
        hashed: Dict[Path, str] = {record.path: record.file_hash for record in hashed_records}
        all_dirs = self._collect_directories(root, sizes.keys())
        nodes = self._build_nodes_bottom_up(all_dirs, sizes, hashed)

        reproducible_nodes = [n for n in nodes.values() if n.reproducible]
        by_signature = group_by(reproducible_nodes, key_fn=lambda n: n.signature)
        candidate_groups = [members for members in by_signature.values() if len(members) > 1]
        top_level_groups = self._drop_nested_duplicates(candidate_groups)

        duplicate_groups = [self._to_duplicate_group(members) for members in top_level_groups]
        return sort_groups_by_size_desc(duplicate_groups)

    @staticmethod
    def _collect_directories(root: Path, file_paths: Iterable[Path]) -> Set[Path]:
        """Собирает все директории внутри просканированного поддерева
        (включая сам root), не поднимаясь выше него."""
        dirs: Set[Path] = {root}
        for path in file_paths:
            current = path.parent
            while current not in dirs:
                dirs.add(current)
                if current == root or current == current.parent:
                    break
                current = current.parent
        return dirs

    def _build_nodes_bottom_up(
        self,
        dirs: Set[Path],
        sizes: Dict[Path, int],
        hashed: Dict[Path, str],
    ) -> Dict[Path, _DirNode]:
        files_by_parent = group_by(sizes.keys(), key_fn=lambda p: p.parent)
        subdirs_by_parent = group_by(dirs, key_fn=lambda d: d.parent)

        nodes: Dict[Path, _DirNode] = {}
        # Сначала самые глубокие директории — так к моменту обработки
        # родителя сигнатуры всех его поддиректорий уже готовы.
        for directory in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
            direct_files = files_by_parent.get(directory, [])
            direct_subdirs = [d for d in subdirs_by_parent.get(directory, []) if d != directory]

            reproducible = all(f in hashed for f in direct_files) and all(
                nodes[d].reproducible for d in direct_subdirs
            )
            total_size = sum(sizes[f] for f in direct_files) + sum(
                nodes[d].total_size for d in direct_subdirs
            )

            signature = None
            if reproducible:
                entries: List[Tuple[str, str, str]] = [
                    (f.name, "f", hashed[f]) for f in direct_files
                ] + [(d.name, "d", nodes[d].signature) for d in direct_subdirs]
                signature = self._hash_entries(entries)

            nodes[directory] = _DirNode(
                path=directory,
                reproducible=reproducible,
                signature=signature,
                total_size=total_size,
            )
        return nodes

    @staticmethod
    def _hash_entries(entries: List[Tuple[str, str, str]]) -> str:
        canonical = "\n".join(f"{kind}:{name}:{content_hash}" for name, kind, content_hash in sorted(entries))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _drop_nested_duplicates(candidate_groups: List[List[_DirNode]]) -> List[List[_DirNode]]:
        matched_paths = {node.path for members in candidate_groups for node in members}

        def has_matched_ancestor(path: Path) -> bool:
            current = path.parent
            while True:
                if current in matched_paths:
                    return True
                if current == current.parent:
                    return False
                current = current.parent

        result = []
        for members in candidate_groups:
            top_level = [n for n in members if not has_matched_ancestor(n.path)]
            if len(top_level) > 1:
                result.append(top_level)
        return result

    @staticmethod
    def _to_duplicate_group(members: List[_DirNode]) -> DuplicateGroup:
        records = []
        for node in members:
            try:
                mtime = node.path.stat().st_mtime
            except OSError:
                mtime = 0.0
            records.append(
                FileRecord(
                    path=node.path,
                    name=node.path.name,
                    size=node.total_size,
                    mtime=mtime,
                    file_hash=node.signature,
                )
            )
        records.sort(key=lambda r: str(r.path))
        return DuplicateGroup(key=(members[0].signature,), records=records, kind=EntryKind.DIRECTORY)


def filter_subsumed_file_groups(
    file_groups: List[DuplicateGroup], directory_groups: List[DuplicateGroup]
) -> List[DuplicateGroup]:
    """Убирает файловые группы, все файлы которых лежат внутри уже
    найденной пары директорий-дубликатов — их дублирование уже показано
    на уровне директории, повторять то же самое на уровне файла было бы
    и шумом в отчёте, и двойным счётом в суммарном "лишнем" размере."""
    duplicated_dirs = [record.path for group in directory_groups for record in group.records]
    if not duplicated_dirs:
        return list(file_groups)

    def is_covered(path: Path) -> bool:
        return any(path == d or d in path.parents for d in duplicated_dirs)

    return [group for group in file_groups if not all(is_covered(r.path) for r in group.records)]
