# dup_stat

Поиск файлов-дубликатов в директории по хешсумме содержимого и (опционально)
дополнительным атрибутам файла — имени и размеру.

## Установка (виртуальное окружение)

Сам пакет не имеет внешних зависимостей (только стандартная библиотека,
Python >= 3.8), но для интерактивной работы рекомендуется venv:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -e .                 # ставит сам dup_stat (даёт команду dup-stat)
pip install -r requirements.txt  # jupyter/pandas/matplotlib для ноутбука
```

## Использование

```bash
python -m dup_stat /path/to/directory
```

Полезные флаги:

| Флаг | Назначение | По умолчанию |
|---|---|---|
| `--algorithm` | алгоритм хеширования (`sha256`, `sha1`, `md5`, `blake2b`, ...) | `sha256` |
| `--match` | критерий совпадения дубликатов: `hash`, `hash+name`, `hash+name+size` | `hash` |
| `--min-size` | игнорировать файлы меньше N байт | `0` |
| `--follow-symlinks` | переходить по симлинкам | выключено |
| `--format` | формат отчёта: `text` или `json` | `text` |
| `--chunk-size` | размер блока чтения файла при хешировании | `1048576` |
| `--save-dir` | дополнительно сохранить результаты локально в директорию (SQLite + pandas.DataFrame) | не сохранять |

Примеры:

```bash
# Только по содержимому (классические дубликаты, независимо от имени файла)
python -m dup_stat ~/Downloads

# Файл считается дубликатом только если совпадают и содержимое, и имя
python -m dup_stat ~/Downloads --match hash+name

# JSON-отчёт для дальнейшей обработки
python -m dup_stat ~/Downloads --format json > report.json

# Сохранить результаты локально: SQLite + pandas.DataFrame с одинаковым timestamp
python -m dup_stat ~/Downloads --save-dir ./results
```

Если пакет установлен (`pip install -e .`), доступна команда `dup-stat`
как эквивалент `python -m dup_stat`.

## Как это работает

1. `RecursiveFileScanner` рекурсивно обходит директорию и отбирает файлы.
2. Файлы группируются по размеру; файлы с уникальным размером сразу
   отбрасываются — они физически не могут иметь одинаковое содержимое,
   и хешировать их не нужно (главная оптимизация производительности).
3. Для оставшихся кандидатов `HashlibFileHasher` считает хешсумму
   (по умолчанию SHA-256), читая файл потоково блоками.
4. `DuplicateKeyStrategy` строит ключ группировки — по умолчанию это
   просто хеш, но можно потребовать совпадения ещё и имени и/или размера.
5. Файлы с одинаковым ключом объединяются в `DuplicateGroup`.
6. `ReportFormatter` (текстовый или JSON) выводит найденные группы и
   суммарный размер "лишних" копий (`wasted_size`) — то есть сколько
   места освободится, если в каждой группе оставить только один файл.
7. Если указан `--save-dir`, `ResultPersistence` дополнительно сохраняет
   результаты локально (см. раздел ниже).

## Сохранение результатов локально (`--save-dir`)

Флаг `--save-dir DIR` сохраняет результаты в двух локальных файлах —
и в SQLite, и как `pandas.DataFrame`, с **одним и тем же timestamp** в
имени, чтобы файлы одного запуска было легко сопоставить друг с
другом:

```
DIR/dup_stat_results_20260823_052141.sqlite3
DIR/dup_stat_results_20260823_052141.pkl
```

- **`.sqlite3`** — таблица `duplicate_files` (`group_id, file_hash, path,
  name, size_bytes, mtime, wasted_bytes`); одна строка на файл. Открыть
  можно любым SQLite-клиентом или `sqlite3` из stdlib:

  ```python
  import sqlite3
  conn = sqlite3.connect("dup_stat_results_20260823_052141.sqlite3")
  conn.execute("SELECT * FROM duplicate_files WHERE wasted_bytes > 1000").fetchall()
  ```

- **`.pkl`** — тот же набор строк, сохранённый как `pandas.DataFrame`
  через `to_pickle` (сохраняет типы колонок в отличие от CSV).
  Загружается одной командой:

  ```python
  import pandas as pd
  df = pd.read_pickle("dup_stat_results_20260823_052141.pkl")
  ```

SQLite выбран как основной формат для локального хранения истории
сканирований: не требует сервера, хранит всё в одном файле, позволяет
делать SQL-запросы и легко читается тем же `pandas` (`pd.read_sql`).

Реализация — в `dup_stat/storage.py`: `ResultExporter` — абстракция
экспортёра (OCP: новый формат, например CSV или Parquet, добавляется
новым классом), `SqliteResultExporter` и `DataFrameResultExporter` — её
реализации, `ResultPersistence` — генерирует общий timestamp и
прогоняет через него все переданные экспортёры.

## Архитектура и принципы SOLID / DRY

Код разбит на модули по зоне ответственности (Single Responsibility):

- `models.py` — данные: `FileRecord`, `DuplicateGroup`.
- `hashing.py` — вычисление хешсумм (`FileHasher` — абстракция, `HashlibFileHasher` — реализация).
- `scanning.py` — обход файловой системы (`FileScanner` / `RecursiveFileScanner`).
- `matching.py` — критерий "что считать дубликатом" (`DuplicateKeyStrategy` / `AttributeKeyStrategy`).
- `finder.py` — оркестрация поиска (`DuplicateFinder`), зависит только от абстракций выше (Dependency Inversion).
- `reporting.py` — форматирование результата (`ReportFormatter` / `TextReportFormatter` / `JsonReportFormatter`).
- `utils.py` — общие мелкие хелперы (например, `human_readable_size`), чтобы не дублировать форматирование размеров в нескольких местах (DRY).
- `storage.py` — сохранение результатов в локальные файлы (`ResultExporter` / `SqliteResultExporter` / `DataFrameResultExporter` / `ResultPersistence`).
- `cli.py` — точка сборки: разбор аргументов и связывание конкретных реализаций (единственное место, знающее обо всех классах сразу).

Каждый интерфейс (`FileHasher`, `FileScanner`, `DuplicateKeyStrategy`,
`ReportFormatter`) можно расширить новой реализацией, не изменяя
существующий код (Open/Closed) — например, добавить хеширование через
`xxhash`, другой обходчик директорий (с игнор-листами) или CSV-отчёт.

## Тесты

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

## Jupyter-ноутбук

`notebooks/dup_stat_demo.ipynb` — интерактивная демонстрация: создаёт
временную директорию с дубликатами, запускает `DuplicateFinder`
программно, показывает результат в виде таблицы `pandas.DataFrame`,
строит график освобождаемого места по группам и сравнивает разные
критерии совпадения (`hash`, `hash+name`, `hash+name+size`).

```bash
source .venv/bin/activate
pip install -r requirements.txt   # если ещё не установлено
jupyter notebook notebooks/dup_stat_demo.ipynb
```
