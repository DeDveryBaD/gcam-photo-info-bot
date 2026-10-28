"""/share_photo: collect photos, fill a 4-field card, publish to the archive channel.

The card is a MEDIA message: it shows the preview of the first (or chosen)
photo plus the questionnaire as its caption, so the user sees what they are
posting. Device / mod / config come from the filename and EXIF whenever
possible, so the user normally only confirms.

Files are forwarded to the archive by file_id, so originals are never
downloaded — only previews need a local copy, and only for documents.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument,
    InputMediaPhoto, Update,
)
from telegram.ext import ContextTypes

from bot.metadata import extract_metadata
from bot.preview import build_preview

log = logging.getLogger(__name__)

# IMG_20261020_195717_FCam_VIBeX6_TEST -> mod=FCam, config=VIBeX6 TEST
NAME_RE = re.compile(
    r"^(?:IMG|VID|GCAM|SCRN|DSC)[_-]?(\d{6,8})[_-]?(\d{4,6})[_-](.+)$", re.I
)
EDIT_MARKERS = ("edit", "edited", "snap", "snapp", "lightroom", "vsco", "picsart")
BRANDS = ("xiaomi ", "poco ", "redmi ", "google ", "samsung ", "oneplus ")
UNKNOWN = "—"

# Previews live for the process lifetime only; Render's disk is ephemeral, but
# the Telegram file_id we obtain on first upload survives re-edits.
PREVIEW_DIR = Path(tempfile.gettempdir()) / "gcam-previews"


@dataclass
class Draft:
    items: list[dict[str, Any]] = field(default_factory=list)
    device: str = UNKNOWN
    mod: str = UNKNOWN
    config: str = UNKNOWN
    edited: bool = False
    send_originals: bool = True
    preview_file_id: str | None = None
    preview_path: str | None = None  # local JPEG awaiting upload, cleared after
    card_chat_id: int | None = None
    card_message_id: int | None = None
    awaiting: str | None = None  # 'device' | 'mod' | 'config' | None

    @property
    def ready(self) -> bool:
        return bool(self.items)


def parse_filename(name: str) -> tuple[str | None, str | None, bool]:
    """Return (mod, config, looks_edited) from a GCam-style filename."""
    stem = Path(name or "").stem
    match = NAME_RE.match(stem)
    lowered_all = stem.lower()
    if not match:
        return None, None, any(m in lowered_all for m in EDIT_MARKERS)
    parts = [p for p in match.group(3).split("_") if p]
    if not parts:
        return None, None, False
    mod = parts[0]
    config = " ".join(parts[1:]) if len(parts) > 1 else None
    joined = " ".join(parts).lower()
    edited = any(m in joined for m in EDIT_MARKERS)
    return mod, config, edited


def clean_model(value: str) -> str:
    # "Xiaomi Mi 10T Pro" -> "Mi 10T Pro": the brand is noise in the card.
    text = " ".join(str(value).split())
    for brand in BRANDS:
        if text.lower().startswith(brand):
            return text[len(brand):]
    return text


async def device_from_exif(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> str | None:
    """Best-effort device name from EXIF. None when the file cannot be fetched."""
    try:
        tg_file = await context.bot.get_file(file_id)
    except Exception:  # noqa: BLE001 - getFile refuses >20 MB
        return None
    if int(tg_file.file_size or 0) > 20 * 1024 * 1024:
        return None
    with tempfile.TemporaryDirectory(prefix="gcam-exif-") as tmp:
        target = Path(tmp) / "input.bin"
        try:
            await tg_file.download_to_drive(custom_path=target)
            result = extract_metadata(target, target.name)
        except Exception:  # noqa: BLE001
            return None
    model = str(result["summary"].get("Модель смартфона") or "")
    if not model or model.startswith("Не определено"):
        return None
    return clean_model(model)


async def document_preview(file_id: str, context: ContextTypes.DEFAULT_TYPE) -> Path | None:
    """Download a document and render a local JPEG preview, or None."""
    try:
        tg_file = await context.bot.get_file(file_id)
    except Exception:  # noqa: BLE001
        return None
    if int(tg_file.file_size or 0) > 20 * 1024 * 1024:
        return None
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gcam-src-") as tmp:
        src = Path(tmp) / "input.bin"
        try:
            await tg_file.download_to_drive(custom_path=src)
        except Exception:  # noqa: BLE001
            return None
        dst = PREVIEW_DIR / f"{abs(hash(file_id))}.jpg"
        return build_preview(src, dst)


def card_caption(draft: Draft) -> str:
    lines = [
        f"📱 {draft.device}",
        f"📸 {draft.mod}",
        f"⚙️ {draft.config}",
        f"✂️ {'Edit' if draft.edited else 'No edit'}",
        "",
        f"🗂 {len(draft.items)} файл(ов)",
    ]
    if draft.awaiting:
        label = {"device": "устройство", "mod": "мод", "config": "конфиг"}[draft.awaiting]
        lines += ["", f"▶️ Введи новое {label} текстом."
                  , "", "/cancel — отменить ввод."]
    return "\n".join(lines)


def card_markup(draft: Draft) -> InlineKeyboardMarkup:
    if draft.awaiting:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ Отменить ввод", callback_data="sh:await:none"),
        ]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📱 {draft.device}", callback_data="sh:device")],
        [InlineKeyboardButton(f"📸 {draft.mod}", callback_data="sh:mod")],
        [InlineKeyboardButton(f"⚙️ {draft.config}", callback_data="sh:config")],
        [InlineKeyboardButton(
            "✂️ Edit" if draft.edited else "✂️ No edit", callback_data="sh:edited")],
        [InlineKeyboardButton(
            "📎 Оригиналы: " + ("да" if draft.send_originals else "нет"),
            callback_data="sh:originals")],
        [
            InlineKeyboardButton("🚀 Опубликовать", callback_data="sh:publish"),
            InlineKeyboardButton("🗑 Отмена", callback_data="sh:cancel"),
        ],
    ])


def pick_preview_source(draft: Draft) -> Any:
    """Cached file_id → freshly rendered preview path → first photo's file_id.

    Documents are skipped in the last step: a document file_id cannot be sent
    as a photo, and Telegram gives no rendition for RAW files.
    """
    if draft.preview_file_id:
        return draft.preview_file_id
    if draft.preview_path and Path(draft.preview_path).exists():
        return Path(draft.preview_path)
    if draft.items and draft.items[0]["is_photo"]:
        return draft.items[0]["file_id"]
    return None


async def show_card(context: ContextTypes.DEFAULT_TYPE, draft: Draft) -> None:
    """Create or update the card message. The preview is the message media."""
    text = "📷 " + card_caption(draft)
    markup = card_markup(draft)

    media = pick_preview_source(draft)

    if draft.card_message_id is None:
        if media is None:
            sent = await context.bot.send_message(
                chat_id=draft.card_chat_id, text=text, reply_markup=markup)
        else:
            sent = await context.bot.send_photo(
                chat_id=draft.card_chat_id, photo=media, caption=text, reply_markup=markup)
        draft.card_message_id = sent.message_id
        if sent.photo:
            draft.preview_file_id = sent.photo[-1].file_id
            draft.preview_path = None  # uploaded once, the file_id is enough now
        return

    try:
        if media is None:
            await context.bot.edit_message_text(
                chat_id=draft.card_chat_id, message_id=draft.card_message_id,
                text=text, reply_markup=markup)
            return
        msg = await context.bot.edit_message_media(
            chat_id=draft.card_chat_id, message_id=draft.card_message_id,
            media=InputMediaPhoto(media=media, caption=text), reply_markup=markup)
        if msg and msg.photo:
            draft.preview_file_id = msg.photo[-1].file_id
            draft.preview_path = None
    except Exception:  # noqa: BLE001 - "message is not modified" is normal
        pass


async def start_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = Draft()
    context.user_data["draft"] = draft
    msg = update.effective_message
    draft.card_chat_id = msg.chat_id
    await msg.reply_text(
        "Режим публикации. Присылай фото — как файл (сохранит EXIF и имя) или как "
        "обычное фото (альбом). Можно несколько подряд.\n\n"
        "Устройство, мод и конфиг подставлю из имени файла и EXIF, превью покажу "
        "на карточке. Поправь что нужно и жми «Опубликовать»."
    )
    draft.card_message_id = None
    await show_card(context, draft)
    from bot.main import SHARE
    return SHARE


async def add_item(context: ContextTypes.DEFAULT_TYPE, draft: Draft, item: dict[str, Any]) -> None:
    draft.items.append(item)
    if item.get("mod") and draft.mod == UNKNOWN:
        draft.mod = item["mod"]
    if item.get("config") and draft.config == UNKNOWN:
        draft.config = item["config"]
    if item.get("device") and draft.device == UNKNOWN:
        draft.device = item["device"]
    if item.get("edited"):
        draft.edited = True

    # First item decides the card preview. Documents (RAW/DNG/JPEG files) have no
    # Telegram rendition, so a local preview is rendered for them.
    if len(draft.items) == 1 and draft.preview_file_id is None:
        if item["is_photo"]:
            draft.preview_path = None
        else:
            path = await document_preview(item["file_id"], context)
            draft.preview_path = str(path) if path else None
    await show_card(context, draft)


def publish_caption(draft: Draft) -> str:
    return (
        f"📱 {draft.device}\n"
        f"📸 {draft.mod}\n"
        f"⚙️ {draft.config}\n"
        f"✂️ {'Edit' if draft.edited else 'No edit'}"
    )


def _media(item: dict[str, Any], cap: str | None):
    if item["is_photo"]:
        return InputMediaPhoto(media=item["file_id"], caption=cap)
    return InputMediaDocument(
        media=item["file_id"], caption=cap, filename=item["filename"],
        supports_streaming=False,
    )


async def publish(context: ContextTypes.DEFAULT_TYPE, draft: Draft) -> str:
    """Send the album to the archive channel. Returns a human-readable line."""
    import os

    target = (os.getenv("ARCHIVE_CHANNEL") or "").strip()
    if not target:
        return (
            "Канал-архив не настроен. Создай канал, добавь бота админом и задай "
            "переменную ARCHIVE_CHANNEL (например @your_channel или -100…)."
        )
    if not draft.ready:
        return "Сначала пришли хотя бы одно фото."

    cap = publish_caption(draft)
    media = [_media(item, cap if i == 0 else None) for i, item in enumerate(draft.items)]
    try:
        sent = await context.bot.send_media_group(chat_id=target, media=media)
    except Exception as exc:  # noqa: BLE001
        log.exception("Publish failed")
        return f"Не опубликовал: {exc}"
    log.info("Published %d items to %s (msgs %s)",
             len(media), target, [m.message_id for m in sent])
    return f"Опубликовано: {len(media)} файл(ов) в {target}."
