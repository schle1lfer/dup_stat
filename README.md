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

Примеры:

```bash
# Только по содержимому (классические дубликаты, независимо от имени файла)
python -m dup_stat ~/Downloads

# Файл считается дубликатом только если совпадают и содержимое, и имя
python -m dup_stat ~/Downloads --match hash+name

# JSON-отчёт для дальнейшей обработки
python -m dup_stat ~/Downloads --format json > report.json
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

## Архитектура и принципы SOLID / DRY

Код разбит на модули по зоне ответственности (Single Responsibility):

- `models.py` — данные: `FileRecord`, `DuplicateGroup`.
- `hashing.py` — вычисление хешсумм (`FileHasher` — абстракция, `HashlibFileHasher` — реализация).
- `scanning.py` — обход файловой системы (`FileScanner` / `RecursiveFileScanner`).
- `matching.py` — критерий "что считать дубликатом" (`DuplicateKeyStrategy` / `AttributeKeyStrategy`).
- `finder.py` — оркестрация поиска (`DuplicateFinder`), зависит только от абстракций выше (Dependency Inversion).
- `reporting.py` — форматирование результата (`ReportFormatter` / `TextReportFormatter` / `JsonReportFormatter`).
- `utils.py` — общие мелкие хелперы (например, `human_readable_size`), чтобы не дублировать форматирование размеров в нескольких местах (DRY).
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
