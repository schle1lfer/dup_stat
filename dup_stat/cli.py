"""Точка сборки приложения: разбор аргументов + композиция зависимостей.

Это единственное место, где создаются конкретные реализации
(RecursiveFileScanner, HashlibFileHasher, ...) и связываются друг с
другом через DuplicateFinder. Остальной код работает с абстракциями.
"""

import argparse
import sys
from pathlib import Path

from .finder import DuplicateFinder
from .hashing import DEFAULT_CHUNK_SIZE, HashlibFileHasher
from .matching import MATCH_PRESETS, create_strategy
from .reporting import JsonReportFormatter, ReportFormatter, TextReportFormatter
from .scanning import RecursiveFileScanner


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dup_stat",
        description="Поиск файлов-дубликатов в директории по хешсумме и другим атрибутам.",
    )
    parser.add_argument("directory", type=Path, help="Директория для сканирования")
    parser.add_argument(
        "--algorithm",
        default="sha256",
        help="Алгоритм хеширования (sha256, sha1, md5, blake2b, ...). По умолчанию sha256.",
    )
    parser.add_argument(
        "--match",
        choices=sorted(MATCH_PRESETS),
        default="hash",
        help=(
            "Критерий совпадения дубликатов: "
            "'hash' — только содержимое (по умолчанию), "
            "'hash+name' — содержимое и имя файла, "
            "'hash+name+size' — содержимое, имя и размер."
        ),
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=0,
        metavar="BYTES",
        help="Игнорировать файлы меньше указанного размера в байтах.",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Переходить по символическим ссылкам при сканировании.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Формат вывода отчёта. По умолчанию text.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        metavar="BYTES",
        help="Размер блока чтения файла при хешировании (в байтах).",
    )
    return parser


def _build_formatter(fmt: str) -> ReportFormatter:
    return JsonReportFormatter() if fmt == "json" else TextReportFormatter()


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.directory.exists():
        print(f"Ошибка: директория '{args.directory}' не найдена.", file=sys.stderr)
        return 1
    if not args.directory.is_dir():
        print(f"Ошибка: '{args.directory}' не является директорией.", file=sys.stderr)
        return 1

    try:
        hasher = HashlibFileHasher(algorithm=args.algorithm, chunk_size=args.chunk_size)
        strategy = create_strategy(args.match)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    scanner = RecursiveFileScanner(follow_symlinks=args.follow_symlinks, min_size=args.min_size)
    finder = DuplicateFinder(scanner=scanner, hasher=hasher, key_strategy=strategy)

    try:
        groups = finder.find(args.directory)
    except (NotADirectoryError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    formatter = _build_formatter(args.format)
    print(formatter.format(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())
