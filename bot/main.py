from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from bot.metadata import extract_metadata, render_txt
from bot.settings import (
    FIELDS, UserSettings, get, render_summary, reset, toggle_field, toggle_files,
)
from bot.share import (
    Draft, UNKNOWN, add_item, clean_model, device_from_exif, parse_filename,
    publish, show_card, start_share,
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "20"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
KEEP_TEMP_FILES = os.getenv("KEEP_TEMP_FILES", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gcam-photo-info")

# Conversation state for /share_photo.
SHARE = "share"

ALLOWED_EXTENSIONS = {    ".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heic", ".heif",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".rw2", ".orf", ".raf",
}

def safe_name(name: str) -> str:
    name = Path(name or "photo").name
    name = re.sub(r"[^A-Za-zА-Яа-яЁё0-9._ -]+", "_", name).strip(" .")
    return (name or "photo")[:120]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я GCam Photo Info — извлекаю технические данные из фотографий.\n\n"
        "Отправь снимок как ФАЙЛ/ДОКУМЕНТ (скрепка → Отправить файл), чтобы Telegram "
        "не вырезал EXIF. Обычные «Фотографии» тоже принимаю, но часть данных в них теряется.\n\n"
        "Отвечаю одним кратким сообщением: смартфон, модуль камеры, зум, ISO, выдержка, дата.\n\n"
        "/menu — выбрать, какие поля показывать и включать ли отчёты файлами.\n"
        "/share_photo — выложить снимок в архив: карточка с превью и анкетой "
        "(устройство, мод, конфиг, обработка).\n"
        "/help — подробности и ограничения."
    )
    if update.message:
        await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Поддерживаются JPEG, PNG, WebP, HEIC/HEIF, TIFF и распространённые RAW "
            "(DNG, CR2, CR3, NEF, ARW, RW2, ORF, RAF).\n"
            f"Лимит файла бота: {MAX_FILE_MB} МБ (может быть ниже лимита Telegram).\n\n"
            "Точность: отправляй оригинал через «Скрепка → Файл». Если EXIF не содержит "
            "зум или название модуля, я скажу «не определено», вместо догадок.\n\n"
            "Приватность: EXIF может хранить GPS и серийный номер. Полные отчёты файлами "
            "по умолчанию выключены — включи их в /menu, если нужен полный дамп.\n\n"
            "/menu — настройки вывода. /share_photo — публикация в архив с анкетой."
        )

async def _download_input(update: Update, context: ContextTypes.DEFAULT_TYPE, target: Path) -> tuple[str, bool, int]:
    """Returns (original filename, is Telegram photo variant, expected size)."""
    msg = update.effective_message
    if msg is None:
        raise ValueError("Сообщение отсутствует")
    if msg.document:
        doc = msg.document
        filename = safe_name(doc.file_name or f"upload_{msg.message_id}")
        size = int(doc.file_size or 0)
        if size and size > MAX_FILE_BYTES:
            raise ValueError(f"Файл превышает лимит {MAX_FILE_MB} МБ.")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            # Trust file content detection by ExifTool less than filename for safety;
            # still accept common MIME image docs even with odd extension.
            if not (doc.mime_type or "").startswith("image/"):
                raise ValueError("Это не похоже на поддерживаемый файл изображения.")
            if not suffix:
                filename += ".img"
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(custom_path=target)
        return filename, False, size
    if msg.photo:
        photo = msg.photo[-1]  # largest available Telegram photo rendition
        size = int(photo.file_size or 0)
        if size and size > MAX_FILE_BYTES:
            raise ValueError(f"Файл превышает лимит {MAX_FILE_MB} МБ.")
        filename = f"telegram_photo_{msg.message_id}.jpg"
        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(custom_path=target)
        return filename, True, size
    raise ValueError("Пришли изображение как фотографию или как файл.")

async def process_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return
    with tempfile.TemporaryDirectory(prefix="gcam-exif-") as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.bin"
        try:
            await msg.reply_text("⏳ Получил файл. Читаю метаданные…")
            filename, was_photo, _ = await _download_input(update, context, input_path)
            if not input_path.exists() or input_path.stat().st_size == 0:
                raise ValueError("Получен пустой файл.")
            # ExifTool reads many formats. It does not execute file content.
            result = await asyncio.to_thread(extract_metadata, input_path, filename)
            sha256 = await asyncio.to_thread(lambda: hashlib.sha256(input_path.read_bytes()).hexdigest())
            result["sha256"] = sha256
            result["source_was_telegram_photo"] = was_photo

            cfg = get(update.effective_user.id if update.effective_user else None)
            body = render_summary(result, cfg)
            caption = f"📷 {body}"
            if was_photo:
                caption += (
                    "\n\n⚠️ Это сжатая версия Telegram-фотографии, часть EXIF могла "
                    "исчезнуть. Пришли снимок как файл — будет точнее."
                )
            caption += "\n\n/menu — что показывать."
            if len(caption) <= 1000:
                await msg.reply_text(caption)
            else:
                await msg.reply_text("📷 Анализ завершён, но описание вышло слишком длинным.")

            if not cfg.files:
                log.info("Processed chat=%s user=%s filename=%s fields=%s files=off",
                         update.effective_chat.id if update.effective_chat else None,
                         update.effective_user.id if update.effective_user else None,
                         filename, result["metadata_fields_count"])
                return

            txt_path = tmp_path / "report.txt"
            json_path = tmp_path / "metadata.json"
            txt_path.write_text(
                render_txt(result) + f"\nSHA-256 файла: {sha256}\n"
                f"Получено как сжатая Telegram-фотография: {'да' if was_photo else 'нет'}\n",
                encoding="utf-8",
            )
            json_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )

            await msg.reply_document(
                document=txt_path.open("rb"),
                filename="report.txt",
                caption="Полный текстовый отчёт",
            )
            await msg.reply_document(
                document=json_path.open("rb"),
                filename="metadata.json",
                caption="Полные извлечённые метаданные в JSON",
            )

            # Send an untouched copy of what the bot received, preserving the
            # bytes/EXIF. A photo-message rendition is already transformed by Telegram.
            original_out = tmp_path / safe_name(filename)
            original_out.write_bytes(input_path.read_bytes())
            await msg.reply_document(
                document=original_out.open("rb"),
                filename=safe_name(filename),
                caption=(
                    "Копия файла, полученного ботом. "
                    + ("Это уже версия, обработанная Telegram." if was_photo
                       else "Байты файла сохранены без изменений.")
                ),
            )
            log.info("Processed chat=%s user=%s filename=%s fields=%s",
                     update.effective_chat.id if update.effective_chat else None,
                     update.effective_user.id if update.effective_user else None,
                     filename, result["metadata_fields_count"])
        except ValueError as exc:
            await msg.reply_text(f"⚠️ {exc}")
        except RuntimeError as exc:
            log.warning("Metadata extraction failed: %s", exc)
            await msg.reply_text(f"Не удалось прочитать метаданные: {exc}")
        except Exception:
            log.exception("Unexpected processing error")
            await msg.reply_text(
                "Произошла внутренняя ошибка при обработке. Попробуй отправить оригинал как файл."
            )

def _menu_markup(cfg: UserSettings) -> InlineKeyboardMarkup:
    rows = []
    for key, label, _ in FIELDS:
        mark = "✅" if key in cfg.fields else "▫️"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"f:{key}")])
    rows.append([InlineKeyboardButton(
        ("✅" if cfg.files else "▫️") + " Прикладывать отчёты файлами",
        callback_data="files")])
    rows.append([
        InlineKeyboardButton("♻️ Сбросить", callback_data="reset"),
        InlineKeyboardButton("✔️ Готово", callback_data="close"),
    ])
    return InlineKeyboardMarkup(rows)


MENU_TEXT = None


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    cfg = get(user.id if user else None)
    text = (
        "Что показывать в ответе на снимок.\n\n"
        "Отчёты файлами: "
        + ("включены — бот пришлёт report.txt, metadata.json и копию снимка."
           if cfg.files else "выключены — только текст.")
    )
    await update.message.reply_text(text, reply_markup=_menu_markup(cfg))


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    user = update.effective_user
    uid = user.id if user else 0
    cfg = get(uid)

    if data.startswith("f:"):
        key = data[2:]
        if not toggle_field(uid, key):
            await q.answer("Нужна хотя бы одна строка в ответе.", show_alert=False)
    elif data == "files":
        toggle_files(uid)
    elif data == "reset":
        reset(uid)
        cfg = get(uid)
        await q.answer("Настройки сброшены.")
    elif data == "close":
        await q.edit_message_text("Готово. Присылай снимок — отвечу по этим настройкам.")
        return

    text = (
        "Что показывать в ответе на снимок.\n\n"
        "Отчёты файлами: "
        + ("включены — бот пришлёт report.txt, metadata.json и копию снимка."
           if cfg.files else "выключены — только текст.")
    )
    await q.edit_message_text(text, reply_markup=_menu_markup(cfg))
    await q.answer()


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Пришли фотографию или изображение как файл. Для полного EXIF лучше использовать «Отправить как файл»."
        )

async def share_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consume photos/documents while a /share_photo draft is open."""
    draft = context.user_data.get("draft")
    msg = update.effective_message
    if not isinstance(draft, Draft) or msg is None:
        return SHARE
    if msg.document:
        file_id = msg.document.file_id
        filename = msg.document.file_name or "photo"
        is_photo = False
    elif msg.photo:
        file_id = msg.photo[-1].file_id
        filename = f"telegram_photo_{msg.message_id}.jpg"
        is_photo = True
    else:
        await msg.reply_text("Пришли фото или файл.")
        return

    mod, config, edited = parse_filename(filename)
    device = None
    if not is_photo:
        device = await device_from_exif(context, file_id)
    await add_item(context, draft, {
        "file_id": file_id, "is_photo": is_photo, "filename": filename,
        "mod": mod, "config": config, "edited": edited, "device": device,
    })
    return SHARE


async def share_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Free-text answers to the 'enter new value' prompt."""
    draft = context.user_data.get("draft")
    if not isinstance(draft, Draft) or not draft.awaiting:
        return SHARE
    value = " ".join((update.effective_message.text or "").split())[:80]
    field = draft.awaiting
    draft.awaiting = None
    if value:
        setattr(draft, {"device": "device", "mod": "mod", "config": "config"}[field],
                clean_model(value) if field == "device" else value)
    await update.effective_message.reply_text("Обновил карточку.")
    await show_card(context, draft)
    return SHARE


async def share_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel inside the conversation: drop the draft."""
    draft = context.user_data.pop("draft", None)
    if isinstance(draft, Draft) and draft.card_message_id:
        await context.bot.edit_message_reply_markup(
            chat_id=draft.card_chat_id, message_id=draft.card_message_id, reply_markup=None)
    await update.effective_message.reply_text("Черновик отменён.")
    return ConversationHandler.END


async def share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    data = q.data or ""
    draft = context.user_data.get("draft")
    if not isinstance(draft, Draft):
        await q.answer("Черновик не найден. Начни заново: /share_photo", show_alert=True)
        return ConversationHandler.END
    action = data.split(":", 1)[1] if ":" in data else data

    if action in ("device", "mod", "config"):
        draft.awaiting = None if draft.awaiting == action else action
    elif action == "await":
        draft.awaiting = None if data.endswith("none") else data.split(":", 2)[-1]
    elif action == "edited":
        draft.edited = not draft.edited
    elif action == "originals":
        draft.send_originals = not draft.send_originals
    elif action == "publish":
        result = await publish(context, draft)
        await q.answer()
        if draft.card_message_id:
            await context.bot.edit_message_reply_markup(
                chat_id=draft.card_chat_id, message_id=draft.card_message_id,
                reply_markup=None)
        await context.bot.send_message(chat_id=q.message.chat_id, text=result)
        if result.startswith("Опубликовано"):
            context.user_data.pop("draft", None)
            return ConversationHandler.END
        return SHARE
    elif action == "cancel":
        context.user_data.pop("draft", None)
        if draft.card_message_id:
            await context.bot.edit_message_reply_markup(
                chat_id=draft.card_chat_id, message_id=draft.card_message_id,
                reply_markup=None)
        await q.answer("Черновик удалён.")
        return ConversationHandler.END

    await q.answer()
    await show_card(context, draft)
    return SHARE


def build_app() -> Application:
    app = Application.builder().token(TOKEN).build()
    # /share_photo owns the chat while a draft is open; ConversationHandler
    # gates on its stored state, so the analyzer below stays untouched.
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("share_photo", start_share)],
        states={SHARE: [
            CallbackQueryHandler(share_callback, pattern=r"^sh:"),
            CommandHandler("cancel", share_cancel),
            MessageHandler(filters.PHOTO | filters.Document.ALL, share_message),
            MessageHandler(filters.TEXT & ~filters.COMMAND, share_text),
        ]},
        fallbacks=[],
        name="share",
        allow_reentry=True,
    ))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(f:|files|reset|close)"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, process_image))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, unsupported))
    return app

def main() -> None:
    if not TOKEN:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN. Скопируй .env.example в .env и укажи токен.")
    import time
    # Render free web services must bind $PORT or the deploy is killed, even
    # though polling is outbound-only. Serve a health endpoint on $PORT.
    port = os.getenv("PORT")
    if port:
        from bot.health import start_health_server
        start_health_server(int(port))
    # Polling: works everywhere, needs no external URL. Outbound getUpdates
    # traffic also keeps the Render free instance awake. Rebuild the app on
    # every restart: run_polling shuts the loop down when it exits.
    while True:
        try:
            log.info("Bot starting (polling)")
            build_app().run_polling(allowed_updates=Update.ALL_TYPES)
            break
        except Exception as exc:  # network blip / Telegram downtime
            log.warning("Polling crashed (%s); restarting in 30 s", exc)
            time.sleep(30)

if __name__ == "__main__":
    main()
