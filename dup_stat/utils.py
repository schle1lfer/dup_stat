"""Мелкие переиспользуемые хелперы (DRY)."""


def human_readable_size(num_bytes: float) -> str:
    """Форматирует размер в байтах в человекочитаемый вид (KB/MB/...)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} EB"
