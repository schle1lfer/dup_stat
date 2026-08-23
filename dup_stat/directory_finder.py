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
    """Внутреннее (служебное) представление одной директории при
    построении дерева "снизу вверх". Имя начинается с подчёркивания —
    значит используется только внутри этого файла, наружу не отдаётся."""

    path: Path
    reproducible: bool  # можно ли для этой директории вообще построить сигнатуру (см. ниже)
    signature: Optional[str]  # сигнатура-хеш всего содержимого директории, если reproducible=True
    total_size: int  # суммарный размер всех файлов внутри, рекурсивно


class DirectoryDuplicateFinder:
    def find(
        self,
        root: Path,
        sizes: Dict[Path, int],
        hashed_records: List[FileRecord],
    ) -> List[DuplicateGroup]:
        """Главный метод: ищет директории-дубликаты внутри root, используя
        уже готовые данные из обычного файлового поиска (ничего заново
        не читает и не хеширует)."""
        # Словарь "путь файла -> его полный хеш", для быстрого поиска.
        hashed: Dict[Path, str] = {record.path: record.file_hash for record in hashed_records}

        all_dirs = self._collect_directories(root, sizes.keys())
        nodes = self._build_nodes_bottom_up(all_dirs, sizes, hashed)

        # Оставляем только те директории, для которых сигнатуру вообще
        # удалось посчитать (см. reproducible в _build_nodes_bottom_up).
        reproducible_nodes = [n for n in nodes.values() if n.reproducible]
        # Группируем директории по сигнатуре — совпадающая сигнатура
        # означает побайтовое совпадение содержимого.
        by_signature = group_by(reproducible_nodes, key_fn=lambda n: n.signature)
        candidate_groups = [members for members in by_signature.values() if len(members) > 1]
        top_level_groups = self._drop_nested_duplicates(candidate_groups)

        duplicate_groups = [self._to_duplicate_group(members) for members in top_level_groups]
        return sort_groups_by_size_desc(duplicate_groups)

    @staticmethod
    def _collect_directories(root: Path, file_paths: Iterable[Path]) -> Set[Path]:
        """Собирает все директории внутри просканированного поддерева
        (включая сам root), не поднимаясь выше него.

        Идея: для каждого файла поднимаемся по родительским папкам
        (path.parent), пока не дойдём до уже добавленной директории или
        до root — так собираются все "уровни" вложенности без пропусков.
        """
        dirs: Set[Path] = {root}  # set — набор без повторов, порядок не важен
        for path in file_paths:
            current = path.parent
            while current not in dirs:
                dirs.add(current)
                if current == root or current == current.parent:
                    break  # дошли до root или до корня файловой системы — останавливаемся
                current = current.parent  # поднимаемся ещё на уровень выше
        return dirs

    def _build_nodes_bottom_up(
        self,
        dirs: Set[Path],
        sizes: Dict[Path, int],
        hashed: Dict[Path, str],
    ) -> Dict[Path, _DirNode]:
        """Считает сигнатуру для каждой директории, начиная с самых
        глубоких (вложенных) и постепенно поднимаясь к более "внешним" —
        точно так же, как считают контрольную сумму дерева файлов
        (Merkle-дерево): сигнатура папки зависит от сигнатур её
        подпапок, поэтому подпапки должны быть посчитаны раньше."""
        # Группируем: для каждой директории — список файлов прямо в ней
        # (files_by_parent) и список вложенных подпапок (subdirs_by_parent).
        files_by_parent = group_by(sizes.keys(), key_fn=lambda p: p.parent)
        subdirs_by_parent = group_by(dirs, key_fn=lambda d: d.parent)

        nodes: Dict[Path, _DirNode] = {}
        # sorted(..., key=len(d.parts), reverse=True) — сортируем директории
        # от самых глубоко вложенных к самым "верхним". Так к моменту
        # обработки родителя сигнатуры всех его поддиректорий уже готовы.
        for directory in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
            direct_files = files_by_parent.get(directory, [])
            direct_subdirs = [d for d in subdirs_by_parent.get(directory, []) if d != directory]

            # reproducible ("воспроизводима") = у этой директории можно
            # надёжно построить сигнатуру. Это возможно, только если у
            # КАЖДОГО файла внутри (прямо здесь или в подпапках) есть
            # посчитанный полный хеш — то есть у него есть "близнец"
            # где-то в дереве (см. пояснение в начале файла). Если хотя
            # бы один файл уникален — сигнатуру строить не из чего,
            # и эта директория точно ни с чем не совпадёт.
            reproducible = all(f in hashed for f in direct_files) and all(
                nodes[d].reproducible for d in direct_subdirs
            )
            total_size = sum(sizes[f] for f in direct_files) + sum(
                nodes[d].total_size for d in direct_subdirs
            )

            signature = None
            if reproducible:
                # Собираем список (имя, тип "f"/"d", хеш) для всех прямых
                # потомков — файлов и подпапок — и хешируем этот список целиком.
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
        """Превращает список (имя, тип, хеш) в один итоговый хеш-строку.

        sorted(entries) нужен, чтобы порядок файлов на диске не влиял на
        результат — иначе одинаковые по содержимому папки могли бы
        получить разные сигнатуры просто из-за порядка чтения.
        """
        canonical = "\n".join(f"{kind}:{name}:{content_hash}" for name, kind, content_hash in sorted(entries))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _drop_nested_duplicates(candidate_groups: List[List[_DirNode]]) -> List[List[_DirNode]]:
        """Убирает из результата "вложенные" дубликаты: если папка A
        целиком совпадает с папкой B, их общая подпапка "sub" тоже
        технически совпадёт сама с собой — но показывать её отдельной
        строкой в отчёте бессмысленно, раз уже показана папка целиком."""
        matched_paths = {node.path for members in candidate_groups for node in members}

        def has_matched_ancestor(path: Path) -> bool:
            """Есть ли у path родительская папка, которая уже сама по себе дубликат."""
            current = path.parent
            while True:
                if current in matched_paths:
                    return True
                if current == current.parent:  # добрались до корня файловой системы
                    return False
                current = current.parent

        result = []
        for members in candidate_groups:
            top_level = [n for n in members if not has_matched_ancestor(n.path)]
            if len(top_level) > 1:  # дубликат — это минимум 2 совпадающие директории
                result.append(top_level)
        return result

    @staticmethod
    def _to_duplicate_group(members: List[_DirNode]) -> DuplicateGroup:
        """Превращает внутренние _DirNode в обычный DuplicateGroup —
        такой же, как для файлов, только kind=DIRECTORY."""
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
                    file_hash=node.signature,  # для директории здесь сигнатура всего поддерева
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
    # Все пути директорий, которые уже признаны дубликатами.
    duplicated_dirs = [record.path for group in directory_groups for record in group.records]
    if not duplicated_dirs:
        return list(file_groups)  # директорий-дубликатов нет — фильтровать нечего

    def is_covered(path: Path) -> bool:
        """Лежит ли path внутри (или совпадает с) одной из дубликат-директорий."""
        # path.parents — список всех родительских папок этого пути.
        return any(path == d or d in path.parents for d in duplicated_dirs)

    # Оставляем только те файловые группы, где НЕ все записи покрыты
    # дубликат-директориями (если покрыты все — вся группа лишняя).
    return [group for group in file_groups if not all(is_covered(r.path) for r in group.records)]
