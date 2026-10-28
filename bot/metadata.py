from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

# ExifTool is deliberately used as a separate executable: it supports many more
# vendor-specific tags and MakerNotes than basic Python EXIF libraries.
OFFSET_TAGS = ("OffsetTimeOriginal", "OffsetTime", "TimeZone", "GMTOffset")
# Capture time only. FileModifyDate/FileInodeChangeDate are deliberately absent:
# they record when the file reached our server (UTC on Render), not when it was taken.
DATE_TAGS = (
    "DateTimeOriginal", "CreateDate", "SubSecDateTimeOriginal", "DateCreated",
    "MediaCreateDate", "TrackCreateDate", "MovieHeaderDate", "ModifyDate",
)
MODEL_TAGS = ("Model", "CameraModelName", "DeviceModelName", "UniqueCameraModel")
MAKE_TAGS = ("Make", "CameraMake", "Manufacturer")
# Model names that legitimately pair with Make "Google" (GCam mods spoof Make).
GOOGLE_MODELS = ("pixel", "nexus", "chromebook", "asus")
MODULE_TAGS = (
    "LensModel", "LensID", "LensType", "CameraID", "CameraType",
    "InternalSerialNumber", "ModuleName", "CameraModule", "LensInfo",
)
ISO_TAGS = ("ISO", "PhotographicSensitivity", "StandardOutputSensitivity")
SHUTTER_TAGS = ("ExposureTime", "ShutterSpeed", "ShutterSpeedValue")
ZOOM_TAGS = ("DigitalZoomRatio", "DigitalZoom", "ZoomRatio", "DigitalZoomFactor")
FOCAL_TAGS = ("FocalLength", "FocalLengthIn35mmFormat", "35mmEquivalent")

SKIP_GROUPS = ("System", "ExifTool", "ICC-header")

def _lookup(tags: dict[str, Any], names: tuple[str, ...],
            exclude_groups: tuple[str, ...] = SKIP_GROUPS) -> tuple[Any, str | None]:
    """Return (value, qualified tag name) for the first name in priority order.

    The System group is excluded by default: FileModifyDate there is the time the
    file landed on OUR server, not the capture time, and exiftool prints it first,
    so an unordered scan would always prefer it.
    """
    for name in names:
        for key, value in tags.items():
            if value in (None, "", "-"):
                continue
            group, _, bare = key.rpartition(":")
            if bare != name:
                continue
            if group and any(g in group for g in exclude_groups):
                continue
            return value, key
    return None, None

def _first(tags: dict[str, Any], names: tuple[str, ...],
           exclude_groups: tuple[str, ...] = SKIP_GROUPS) -> Any:
    return _lookup(tags, names, exclude_groups)[0]

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

def _offset_hours(tags: dict[str, Any]) -> float | None:
    """UTC offset stored in EXIF, e.g. "+05:00" -> 5.0. None when absent."""
    raw = _first(tags, OFFSET_TAGS)
    if raw is None:
        return None
    match = re.search(r"([+-])(\d{1,2}):?(\d{2})?", str(raw))
    if not match:
        return None
    sign = -1 if match.group(1) == "-" else 1
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    return sign * (hours + minutes / 60)


def reconcile_capture_time(date_value: Any, tags: dict[str, Any],
                           filename: str) -> tuple[str, str, str | None]:
    """Return (display, year, note).

    EXIF has no mandatory timezone for DateTimeOriginal, and GCam mods disagree:
    some write device-local time, others write UTC. The filename stamp
    (IMG_YYYYMMDD_HHMMSS) is generated locally by the camera, so it is the only
    in-file witness of wall-clock time. When the two disagree by whole hours,
    report the filename time and say why.
    """
    date_text, year = _normalise_date(date_value)
    if date_value is None:
        return date_text, year, None

    stamp = re.search(r"(\d{4})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})", filename or "")
    if not stamp:
        # No filename witness. An OffsetTime that merely confirms local time is
        # not a discrepancy and must not be reported.
        return date_text, year, None

    from datetime import datetime as _dt

    try:
        file_dt = _dt(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3)),
                      int(stamp.group(4)), int(stamp.group(5)), int(stamp.group(6)))
    except ValueError:
        return date_text, year, None
    try:
        exif_dt = _dt.strptime(str(date_value)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return date_text, year, None

    delta = (file_dt - exif_dt).total_seconds() / 3600
    if abs(delta) < 1:
        return date_text, year, None
    if abs(delta - round(delta)) > 0.02:
        return (file_dt.strftime("%d.%m.%Y %H:%M:%S"), str(file_dt.year),
                f"EXIF '{date_text}' расходится с именем файла не на целые часы")
    return (file_dt.strftime("%d.%m.%Y %H:%M:%S"), str(file_dt.year),
            f"EXIF хранил UTC ({date_text}); по имени файла снято в "
            f"{file_dt.strftime('%H:%M:%S')} локального времени ({delta:+g} ч)")


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

def _model(tags: dict[str, Any]) -> tuple[str, str | None]:
    """Return (display, note). Merged brand is only used when it is credible."""
    make = _first(tags, MAKE_TAGS)
    model = _first(tags, MODEL_TAGS)
    if not model:
        return (str(make) if make else "Не определено (поле модели отсутствует)"), None
    model_text = " ".join(str(model).split())
    if not make:
        return model_text, None
    make_text = str(make).strip()
    model_low = model_text.lower()
    if make_text.lower() in model_low:
        return model_text, None
    # A brand that contradicts the model name is a mod spoof, not a fact.
    credible = make_text.lower() in ("google",) and any(
        g in model_low for g in GOOGLE_MODELS)
    if not credible and make_text.lower() not in ("google",):
        return f"{make_text} {model_text}", None
    if credible:
        return model_text, None
    return model_text, (
        f"Производитель в EXIF указан как «{make_text}», что противоречит модели "
        f"«{model_text}»: часть GCam-модов подменяет эту строку")

def _iso(tags: dict[str, Any]) -> str:
    value = _first(tags, ISO_TAGS)
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    return str(value) if value is not None else "Не определено"

# exiftool prints "YCbCr4:4:4 (1 1)"; some builds emit "4 2 0". \b cannot guard
# the leading digit: inside "YCbCr4" there is no word boundary before it.
SUBSAMPLING_RE = re.compile(
    r"(?<!\d)(4)(?:\s*[:\-]\s*|\s+)([024])(?:\s*[:\-]\s*|\s+)([024])?(?!\d)"
)

def _subsampling_match(text: str) -> str | None:
    match = SUBSAMPLING_RE.search(text)
    if not match:
        return None
    parts = [g for g in match.groups() if g is not None]
    return ":".join(parts)


def _subsampling(tags: dict[str, Any]) -> str:
    """Chroma subsampling, e.g. "4:4:4".

    Two independent witnesses: the JFIF/EXIF YCbCrSubSampling tag and the
    ImageDescription string some GCam mods write ("sRGB.YCbCr 4:4:4.Zoom: 1.05x").
    RAW containers carry neither, so they report as undefined and stay hidden.
    """
    for group in ("File", "ExifIFD", "JFIF", "IFD0"):
        for key, value in tags.items():
            if not key.endswith("YCbCrSubSampling") or value in (None, "", "-"):
                continue
            found = _subsampling_match(str(value))
            if found:
                return found
    for key in ("ImageDescription", "Make", "Model"):
        for group_key, value in tags.items():
            if not group_key.endswith(key) or value in (None, "", "-"):
                continue
            found = _subsampling_match(str(value))
            if found:
                return found
    return "Не определено"


VERSIONISH = re.compile(r"^v?\d+(?:[._-]\d+)*", re.I)

def mod_from_software(tags: dict[str, Any]) -> str | None:
    """Mod name from the Software tag: "HDR+ 1.0.388784762zd" -> "HDR+".

    GCam mods sign their output here, so this is a real fallback when the
    filename carries no mod (Telegram renames files on compressed photos).
    """
    raw = _first(tags, ("Software",))
    if not raw:
        return None
    tokens = str(raw).split()
    name: list[str] = []
    for token in tokens:
        if VERSIONISH.match(token):
            break
        name.append(token)
    if not name:
        return None
    joined = " ".join(name).strip(" -_")
    # A bare "Google"/"Samsung" is a brand, not a mod.
    if joined.lower() in ("google", "samsung", "xiaomi", "oneplus", "asus"):
        return None
    return joined[:40] if joined else None


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
    model_display, model_note = _model(tags)
    file_name = original_filename or str(_first(tags, ("FileName",)) or path.name)
    date_text, year, time_note = reconcile_capture_time(date_value, tags, file_name)
    _time_value, time_source = _lookup(tags, DATE_TAGS)
    file_type = _first(tags, ("FileType", "MIMEType")) or path.suffix.lstrip(".").upper() or "Не определён"

    summary = {
        "Имя файла": file_name,
        "Модель смартфона": model_display,
        "Модуль камеры": _module(tags),
        "Зум": _display_zoom(tags),
        "ISO": _iso(tags),
        "Выдержка": _human_shutter(_first(tags, SHUTTER_TAGS), tags),
        "Дата съёмки": date_text,
        "Год съёмки": year,
        "Формат": str(file_type),
        "Субсэмплирование": _subsampling(tags),
        "Размер изображения": str(
            _lookup(tags, ("ImageSize",), exclude_groups=()) [0]
            or _first(tags, ("ImageWidth",)) or "Не определено"),
        "Пояснение времени": time_note or "",
        "Пояснение модели": model_note or "",
        "Примечание": "Значения показаны только при наличии соответствующих данных; модуль камеры и зум могут не записываться в EXIF.",
    }
    return {
        "summary": summary,
        "time_source_tag": time_source,
        "mod_from_software": mod_from_software(tags),
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
