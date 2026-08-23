"""Объединение новых результатов с ранее сохранёнными (флаг --append-timestamp).

Позволяет накапливать историю дубликатов между запусками: результаты
нового прогона добавляются к уже сохранённому набору (по указанному
timestamp), объединённые записи пересобираются в группы заново и
пересортировываются — точно так же, как обычные результаты (см.
finder.py / models.py), просто на объединённом наборе.

Старые файлы результата при этом никак не меняются: слияние только
ЧИТАЕТ прежний JSON, а объединённый результат сохраняется уже под
новым timestamp — это делает cli.py через обычный storage.py.
"""

import json
from pathlib import Path
from typing import Dict, List

from .models import DuplicateGroup, EntryKind, FileRecord, sort_groups_by_size_desc
from .storage import DEFAULT_FILENAME_PREFIX
from .utils import group_by


def load_previous_rows(
    save_dir: Path, timestamp: str, filename_prefix: str = DEFAULT_FILENAME_PREFIX
) -> List[Dict]:
    """Читает ранее сохранённые результаты по timestamp.

    Именно JSON (а не .sqlite3/.pkl/.xlsx), потому что его чтение не
    требует ни pandas, ни sqlite3-запросов — просто json.load.
    """
    json_path = Path(save_dir) / f"{filename_prefix}_{timestamp}.json"
    if not json_path.exists():
        raise FileNotFoundError(
            f"Не найден файл предыдущих результатов: {json_path}. "
            "Проверьте --save-dir и правильность timestamp."
        )
    with json_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _row_to_record(row: Dict) -> FileRecord:
    """Обратное преобразование: плоская строка результата -> FileRecord
    (обратная операция к _rows_from_groups в storage.py)."""
    return FileRecord(
        path=Path(row["path"]),
        name=row["name"],
        size=row["size_bytes"],
        mtime=row["mtime"],
        file_hash=row["file_hash"],
    )


def merge_with_previous(groups: List[DuplicateGroup], previous_rows: List[Dict]) -> List[DuplicateGroup]:
    """Объединяет текущие найденные группы с ранее сохранёнными строками
    и пересобирает итоговые группы дубликатов заново, отсортированными
    по размеру (по убыванию) — как и всегда.

    Если один и тот же путь встречается и в старых, и в новых данных
    (например, при повторном сканировании той же директории) — в
    объединённом результате остаётся более свежая запись из текущего
    прогона, а не устаревшая.
    """
    # Сначала старые записи, потом новые: при совпадении пути новые
    # запишутся поверх старых в цикле ниже (более позднее значение в
    # словаре побеждает).
    entries = [(_row_to_record(row), EntryKind(row["kind"])) for row in previous_rows]
    entries += [(record, group.kind) for group in groups for record in group.records]

    # Группируем заново по (тип записи, хеш) — так итоговые группы не
    # зависят от того, в каком именно прогоне была найдена каждая запись.
    grouped = group_by(entries, key_fn=lambda item: (item[1], item[0].file_hash))

    merged_groups: List[DuplicateGroup] = []
    for (kind, file_hash), items in grouped.items():
        by_path: Dict[Path, FileRecord] = {}
        for record, _kind in items:
            by_path[record.path] = record  # дубликат пути -> оставляем последнюю (более свежую) запись
        records = sorted(by_path.values(), key=lambda r: str(r.path))
        if len(records) > 1:  # запись без пары — больше не дубликат, в отчёт не попадает
            merged_groups.append(DuplicateGroup(key=(kind.value, file_hash), records=records, kind=kind))

    return sort_groups_by_size_desc(merged_groups)
