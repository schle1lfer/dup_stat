# Этот файл позволяет запускать пакет командой `python -m dup_stat ...`
# Python сам ищет и выполняет именно __main__.py, когда пакет запускают
# через ключ -m.
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
