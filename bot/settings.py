"""Per-user output settings: which fields to show, and whether to attach files.

State lives in memory for the lifetime of the process. Render's free tier has
no persistent disk, so a redeploy resets it to defaults — that is acceptable:
the menu is one tap away.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# (key, label in menu, key in result["summary"])
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("model", "Смартфон", "Модель смартфона"),
    ("module", "Модуль камеры", "Модуль камеры"),
    ("zoom", "Зум", "Зум"),
    ("iso", "ISO", "ISO"),
    ("shutter", "Выдержка", "Выдержка"),
    ("date", "Дата съёмки", "Дата съёмки"),
    ("year", "Год съёмки", "Год съёмки"),
    ("filename", "Имя файла", "Имя файла"),
    ("format", "Формат файла", "Формат"),
    ("size", "Размер снимка", "Размер изображения"),
    ("subsampling", "Субсэмплирование", "Субсэмплирование"),
    ("timenote", "Пояснение ко времени", "Пояснение времени"),
    ("modelnote", "Пояснение к модели", "Пояснение модели"),
)

# Emoji prefix per field, kept in the message body.
ICONS: dict[str, str] = {
    "model": "📱", "module": "📸", "zoom": "🔎", "iso": "🎚",
    "shutter": "⏱", "date": "📅", "year": "📆", "filename": "📁",
    "format": "🗂", "size": "📐", "subsampling": "🎨", "timenote": "🕰", "modelnote": "⚠️",
}

# Subsampling joins the defaults: 4:4:4 vs 4:2:0 is the cheapest way to tell a
# mod's chroma pipeline apart without opening the file.
DEFAULT_FIELDS = frozenset(
    {"model", "module", "zoom", "iso", "shutter", "date", "subsampling"}
)
LABELS = {key: label for key, label, _ in FIELDS}
SUMMARY_KEYS = {key: summary_key for key, _, summary_key in FIELDS}


@dataclass
class UserSettings:
    fields: set[str] = field(default_factory=lambda: set(DEFAULT_FIELDS))
    files: bool = False  # attach report.txt / metadata.json / file copy


_STORE: dict[int, UserSettings] = {}


def get(user_id: int | None) -> UserSettings:
    if user_id is None:
        return UserSettings()
    return _STORE.setdefault(user_id, UserSettings())


def toggle_field(user_id: int, key: str) -> bool:
    cfg = get(user_id)
    if key not in SUMMARY_KEYS:
        return False
    if key in cfg.fields:
        if len(cfg.fields) == 1:
            return False  # keep at least one line in the summary
        cfg.fields.discard(key)
    else:
        cfg.fields.add(key)
    return True


def toggle_files(user_id: int) -> bool:
    cfg = get(user_id)
    cfg.files = not cfg.files
    return cfg.files


def reset(user_id: int) -> None:
    _STORE[user_id] = UserSettings()


def render_summary(result: dict[str, Any], cfg: UserSettings) -> str:
    """Compact one-line-per-field text. Empty lines are skipped."""
    summary = result["summary"]
    lines = []
    for key, _label, summary_key in FIELDS:
        if key not in cfg.fields:
            continue
        value = str(summary.get(summary_key, "")).strip()
        # Placeholder prose ("Название модуля отсутствует; …") is noise in the
        # compact card; the explicit "не определено" forms are equally hidden.
        if not value or "не определ" in value.lower() or "отсутствует" in value.lower():
            continue
        lines.append(f"{ICONS[key]} {summary_key}: {value}")
    return "\n".join(lines) if lines else "ℹ️ Определяемых параметров в метаданных нет."
