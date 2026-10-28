from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

# ExifTool is deliberately used as a separate executable: it supports many more
# vendor-specific tags and MakerNotes than basic Python EXIF libraries.
DATE_TAGS = (
    "DateTimeOriginal", "CreateDate", "DateCreated", "MediaCreateDate",
    "TrackCreateDate", "ModifyDate", "FileModifyDate",
)
MODEL_TAGS = ("Model", "CameraModelName", "DeviceModelName", "UniqueCameraModel")
MAKE_TAGS = ("Make", "CameraMake", "Manufacturer")
MODULE_TAGS = (
    "LensModel", "LensID", "LensType", "CameraID", "CameraType",
    "InternalSerialNumber", "ModuleName", "CameraModule", "LensInfo",
)
ISO_TAGS = ("ISO", "PhotographicSensitivity", "StandardOutputSensitivity")
SHUTTER_TAGS = ("ExposureTime", "ShutterSpeed", "ShutterSpeedValue")
ZOOM_TAGS = ("DigitalZoomRatio", "DigitalZoom", "ZoomRatio", "DigitalZoomFactor")
FOCAL_TAGS = ("FocalLength", "FocalLengthIn35mmFormat", "35mmEquivalent")

def _first(tags: dict[str, Any], names: tuple[str, ...]) -> Any:
    # Prefer exact tag names but accept ExifTool's Group:Tag names.
    for name in names:
        if name in tags and tags[name] not in (None, "", "-"):
            return tags[name]
    for key, value in tags.items():
        if value in (None, "", "-"):
            continue
        if key.split(":")[-1] in names:
            return value
    return None

def _human_shutter(value: Any, tags: dict[str, Any]) -> str:
    if value is None:
        value = _first(tags, ("ShutterSpeedValue",))
    if value is None:
        return "Не определено"
    # ExifTool usually returns readable ExposureTime such as "1/120".
    s = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        try:
            f = float(s)
            if f > 0 and f < 1:
                denominator = round(1 / f)
                if denominator and abs(1 / denominator - f) < 0.005:
                    return f"1/{denominator} с"
                return f"{f:.4g} с"
            return f"{f:g} с"
        except ValueError:
            pass
    return s + ("" if s.endswith(("с", "sec", "s")) else " с")

def _normalise_date(value: Any) -> tuple[str, str]:
    if value is None:
        return "Не определено", "Не определён"
    s = str(value).strip()
    # Keep original value in report, but extract year where possible.
    match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", s)
    year = match.group(1) if match else "Не определён"
    # Common EXIF date: YYYY:MM:DD HH:MM:SS
    try:
        parsed = datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
        return parsed.strftime("%d.%m.%Y %H:%M:%S"), str(parsed.year)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return parsed.strftime("%d.%m.%Y %H:%M:%S"), str(parsed.year)
        except ValueError:
            return s, year

def _display_zoom(tags: dict[str, Any]) -> str:
    value = _first(tags, ZOOM_TAGS)
    if value is not None:
        try:
            numeric = float(str(value).split()[0])
            if numeric == 0:
                return "Не определено (в метаданных указано 0)"
            return f"{numeric:g}× (значение из метаданных)"
        except (ValueError, TypeError):
            return f"{value} (значение из метаданных)"
    # Focal length is useful context but is not enough to reliably infer zoom.
    focal = _first(tags, ("FocalLength",))
    equiv = _first(tags, ("FocalLengthIn35mmFormat",))
    extras = []
    if focal is not None:
        extras.append(f"фокусное расстояние {focal}")
    if equiv is not None:
        extras.append(f"эквивалент 35 мм {equiv}")
    if extras:
        return "Коэффициент зума не записан; " + ", ".join(extras)
    return "Не определено (поле зума отсутствует)"

def _module(tags: dict[str, Any]) -> str:
    parts = []
    for tag in MODULE_TAGS:
        value = _first(tags, (tag,))
        if value is not None and str(value) not in parts:
            parts.append(str(value))
    if parts:
        return "; ".join(parts)
    # Focal length alone does not uniquely identify a physical camera module.
    focal = _first(tags, ("FocalLength",))
    if focal is not None:
        return f"Название модуля отсутствует; фокусное расстояние: {focal}"
    return "Не определено (EXIF не содержит названия модуля)"

def _model(tags: dict[str, Any]) -> str:
    make = _first(tags, MAKE_TAGS)
    model = _first(tags, MODEL_TAGS)
    if make and model:
        if str(make).lower() in str(model).lower():
            return str(model)
        return f"{make} {model}"
    return str(model or make or "Не определено (поле модели отсутствует)")

def _iso(tags: dict[str, Any]) -> str:
    value = _first(tags, ISO_TAGS)
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return str(value) if value is not None else "Не определено"

def extract_metadata(path: Path, original_filename: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["exiftool", "-json", "-G1", "-a", "-s", "-u", "-ee", str(path)],
            check=False, capture_output=True, text=True, timeout=45,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ExifTool не установлен или недоступен в PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Анализ файла превысил лимит времени") from exc

    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        detail = proc.stderr.strip() or "ExifTool не смог прочитать файл"
        raise RuntimeError(detail[:500])

    try:
        records = json.loads(proc.stdout)
        raw = records[0] if records else {}
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise RuntimeError("Не удалось разобрать ответ ExifTool") from exc

    # Flattened lookup preserves group-qualified names; raw data remains complete.
    tags = dict(raw)
    date_value = _first(tags, DATE_TAGS)
    date_text, year = _normalise_date(date_value)
    file_name = original_filename or str(_first(tags, ("FileName",)) or path.name)
    file_type = _first(tags, ("FileType", "MIMEType")) or path.suffix.lstrip(".").upper() or "Не определён"

    summary = {
        "Имя файла": file_name,
        "Модель смартфона": _model(tags),
        "Модуль камеры": _module(tags),
        "Зум": _display_zoom(tags),
        "ISO": _iso(tags),
        "Выдержка": _human_shutter(_first(tags, SHUTTER_TAGS), tags),
        "Дата съёмки": date_text,
        "Год съёмки": year,
        "Формат": str(file_type),
        "Размер изображения": str(_first(tags, ("ImageSize", "ImageWidth")) or "Не определено"),
        "Примечание": "Значения показаны только при наличии соответствующих данных; модуль камеры и зум могут не записываться в EXIF.",
    }
    return {
        "summary": summary,
        "all_metadata": raw,
        "metadata_fields_count": len(raw),
        "exiftool_warning": proc.stderr.strip() or None,
    }

def render_txt(result: dict[str, Any]) -> str:
    lines = ["ТЕХНИЧЕСКИЕ ДАННЫЕ ФОТОГРАФИИ", "=" * 34, ""]
    for key, value in result["summary"].items():
        lines.append(f"{key}: {value}")
    lines += [
        "", f"Количество извлечённых полей: {result['metadata_fields_count']}",
        "", "ПОЛНЫЕ МЕТАДАННЫЕ EXIFTOOL", "=" * 34,
        json.dumps(result["all_metadata"], ensure_ascii=False, indent=2),
    ]
    if result.get("exiftool_warning"):
        lines += ["", "ПРЕДУПРЕЖДЕНИЕ EXIFTOOL", result["exiftool_warning"]]
    return "\n".join(lines) + "\n"
