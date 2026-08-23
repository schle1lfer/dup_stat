"""Точка сборки приложения: разбор аргументов + композиция зависимостей.

Это единственное место, где создаются конкретные реализации
(RecursiveFileScanner, HashlibFileHasher, ...) и связываются друг с
другом через DuplicateFinder. Остальной код работает с абстракциями.
"""

import argparse
import sys
from pathlib import Path

from .directory_finder import DirectoryDuplicateFinder, filter_subsumed_file_groups
from .finder import DEFAULT_PARTIAL_HASH_BYTES, DuplicateFinder
from .hash_computation import ThreadPoolHashComputation
from .hashing import DEFAULT_CHUNK_SIZE, HashlibFileHasher
from .matching import MATCH_PRESETS, create_strategy
from .models import sort_groups_by_size_desc
from .progress import NullProgressReporter, ProgressReporter, SpinnerProgressReporter
from .reporting import JsonReportFormatter, ReportFormatter, TextReportFormatter
from .result_merge import load_previous_rows, merge_with_previous
from .scanning import RecursiveFileScanner
from .storage import (
    DataFrameResultExporter,
    ExcelResultExporter,
    JsonResultExporter,
    ResultPersistence,
    SqliteResultExporter,
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Описывает все аргументы командной строки: как их называть в
    терминале (--match, --workers, ...), какого они типа и что делают.
    argparse сам разберёт введённую пользователем строку по этому описанию."""
    parser = argparse.ArgumentParser(
        prog="dup_stat",
        description=(
            "Поиск файлов-дубликатов в директории по хешсумме и другим атрибутам, "
            "а также директорий, полностью дублирующих друг друга (--no-dirs отключает)."
        ),
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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Число потоков для параллельного хеширования файлов. "
            "По умолчанию — автоматически (как в ThreadPoolExecutor). "
            "Укажите 1, чтобы отключить параллелизм."
        ),
    )
    parser.add_argument(
        "--partial-hash-bytes",
        type=int,
        default=DEFAULT_PARTIAL_HASH_BYTES,
        metavar="BYTES",
        help=(
            "Размер префикса файла для предварительного хеша — отсеивает "
            "непохожие файлы без чтения их целиком (ускоряет работу на "
            f"больших файлах). По умолчанию {DEFAULT_PARTIAL_HASH_BYTES} байт. "
            "0 — отключить эту стадию."
        ),
    )
    parser.add_argument(
        "--no-dirs",
        dest="find_directories",
        action="store_false",
        default=True,
        help=(
            "Не искать директории-полные-дубликаты (по умолчанию включено). "
            "Директория считается дубликатом другой, если у них полностью "
            "совпадают структура и содержимое всех файлов рекурсивно."
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help=(
            "Не показывать индикатор прогресса (крутящееся колёсико, проценты, "
            "N из M файлов) во время хеширования. По умолчанию показывается, "
            "если вывод идёт в терминал."
        ),
    )
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Дополнительно сохранить результаты локально в DIR сразу в "
            "4 форматах с одинаковым timestamp в имени: "
            "SQLite ('..._<timestamp>.sqlite3'), JSON ('..._<timestamp>.json'), "
            "pandas.DataFrame ('..._<timestamp>.pkl') и Excel ('..._<timestamp>.xlsx'). "
            "Форматы, для которых не хватает опциональной зависимости "
            "(pandas/openpyxl), пропускаются без остановки остальных."
        ),
    )
    parser.add_argument(
        "--append-timestamp",
        type=str,
        default=None,
        metavar="TIMESTAMP",
        help=(
            "Добавить результаты этого запуска к уже сохранённым — по "
            "timestamp прежнего сохранения (например 20260823_071202, "
            "как в имени файла 'dup_stat_results_20260823_071202.json'). "
            "Прежние записи и новые объединяются, группы пересобираются "
            "заново и сортируются вместе. Прежние файлы результата НЕ "
            "изменяются — объединённый результат сохраняется под новым "
            "timestamp. Требует --save-dir (там же должны лежать прежние "
            "файлы результата, включая .json)."
        ),
    )
    return parser


def _build_formatter(fmt: str) -> ReportFormatter:
    """По названию формата ('text'/'json') выбирает подходящий класс форматтера."""
    return JsonReportFormatter() if fmt == "json" else TextReportFormatter()


def _build_progress_reporter(no_progress: bool) -> ProgressReporter:
    """Индикатор прогресса показываем, только если явно не отключили
    флагом и вывод действительно идёт в терминал (не перенаправлен в
    файл/пайп) — иначе управляющие символы \\r засорили бы лог."""
    if no_progress or not sys.stderr.isatty():
        return NullProgressReporter()
    return SpinnerProgressReporter()


def main(argv=None) -> int:
    """Точка входа программы. argv=None означает "взять аргументы из
    командной строки", но можно передать список строк явно (удобно для тестов).

    Возвращает код завершения: 0 — успех, 1 — ошибка (так принято в CLI-утилитах)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)  # разбираем аргументы командной строки в объект args

    # Проверяем входные данные заранее, чтобы дать понятную ошибку, а не
    # упасть где-то в середине сканирования с непонятным traceback.
    if not args.directory.exists():
        print(f"Ошибка: директория '{args.directory}' не найдена.", file=sys.stderr)
        return 1
    if not args.directory.is_dir():
        print(f"Ошибка: '{args.directory}' не является директорией.", file=sys.stderr)
        return 1

    try:
        # Здесь создаются конкретные реализации (см. docstring модуля) —
        # это и есть "точка сборки": единственное место, где всё
        # соединяется воедино.
        hasher = HashlibFileHasher(algorithm=args.algorithm, chunk_size=args.chunk_size)
        strategy = create_strategy(args.match)
        progress = _build_progress_reporter(args.no_progress)
        hash_computation = ThreadPoolHashComputation(max_workers=args.workers, progress=progress)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    scanner = RecursiveFileScanner(follow_symlinks=args.follow_symlinks, min_size=args.min_size)
    finder = DuplicateFinder(
        scanner=scanner,
        hasher=hasher,
        key_strategy=strategy,
        hash_computation=hash_computation,
        partial_hash_bytes=args.partial_hash_bytes,
    )

    try:
        scan_result = finder.find_files(args.directory)  # запускаем поиск файловых дубликатов
    except (NotADirectoryError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    groups = list(scan_result.groups)
    if args.find_directories:  # включено по умолчанию, выключается флагом --no-dirs
        directory_groups = DirectoryDuplicateFinder().find(
            args.directory, scan_result.sizes, scan_result.hashed_records
        )
        # Убираем файловые группы, уже "объяснённые" найденной директорией-дубликатом.
        file_groups = filter_subsumed_file_groups(groups, directory_groups)
        groups = sort_groups_by_size_desc(file_groups + directory_groups)

    if args.append_timestamp is not None:
        if args.save_dir is None:
            print("Ошибка: --append-timestamp требует --save-dir.", file=sys.stderr)
            return 1
        try:
            previous_rows = load_previous_rows(args.save_dir, args.append_timestamp)
        except FileNotFoundError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
        groups = merge_with_previous(groups, previous_rows)

    formatter = _build_formatter(args.format)
    print(formatter.format(groups))

    if args.save_dir is not None:
        # SQLite и JSON не требуют опциональных зависимостей — идут первыми,
        # чтобы точно сохраниться, даже если pandas/openpyxl не установлены.
        persistence = ResultPersistence(
            [
                SqliteResultExporter(),
                JsonResultExporter(),
                DataFrameResultExporter(),
                ExcelResultExporter(),
            ]
        )
        save_result = persistence.save_all(groups, args.save_dir)
        for path in save_result.saved:
            # В stderr, а не в stdout — чтобы не мешать выводу отчёта,
            # если его перенаправляют в файл (> report.txt).
            print(f"Сохранено: {path}", file=sys.stderr)
        for message in save_result.skipped:
            print(f"Пропущено: {message}", file=sys.stderr)

    return 0


if __name__ == "__main__":  # True, только если файл запущен напрямую (python cli.py), а не импортирован
    sys.exit(main())
