from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

from bot.metadata import extract_metadata, render_txt

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

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heic", ".heif",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".rw2", ".orf", ".raf",
}

def safe_name(name: str) -> str:
    name = Path(name or "photo").name
    name = re.sub(r"[^A-Za-zА-Яа-яЁё0-9._ -]+", "_", name).strip(" .")
    return (name or "photo")[:120]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я GCam Photo Info — извлекаю технические данные из фотографий.\n\n"
        "Отправь изображение как ФАЙЛ/ДОКУМЕНТ, чтобы Telegram не сжимал его. "
        "Обычные фотографии тоже принимаются, но Telegram может удалить часть EXIF.\n\n"
        "Я покажу модель смартфона, сведения о модуле камеры, зум, ISO, выдержку, "
        "дату/год и имя файла, а также отправлю полный TXT-отчёт и JSON с метаданными.\n\n"
        "Команда /help — подробности."
    )
    if update.message:
        await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Поддерживаются JPEG, PNG, WebP, HEIC/HEIF, TIFF и распространённые RAW.\n"
            f"Лимит файла бота: {MAX_FILE_MB} МБ (может быть ниже лимита Telegram).\n\n"
            "Для максимально точного анализа отправляй оригинал через «Скрепка → Файл», "
            "а не как сжатую фотографию. Если EXIF не содержит зум или название модуля, "
            "я укажу, что значение не определено, вместо догадок.\n\n"
            "Примечание: EXIF может содержать GPS и серийные номера; полный JSON-отчёт "
            "может включать эти поля. Не пересылай его публично, если не хочешь раскрывать их."
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

            summary = result["summary"]
            caption = (
                "📷 Технические данные фотографии\n"
                f"📱 Смартфон: {summary['Модель смартфона']}\n"
                f"📸 Модуль камеры: {summary['Модуль камеры']}\n"
                f"🔎 Зум: {summary['Зум']}\n"
                f"🎚 ISO: {summary['ISO']}\n"
                f"⏱ Выдержка: {summary['Выдержка']}\n"
                f"📅 Дата: {summary['Дата съёмки']} (год: {summary['Год съёмки']})\n"
                f"📁 Файл: {summary['Имя файла']}"
            )
            if was_photo:
                caption += (
                    "\n\n⚠️ Получена версия Telegram-фотографии. "
                    "Для анализа оригинальных метаданных отправляй снимок как файл."
                )
            # Keep caption within Telegram's limit. If unusually long fields exist,
            # send the concise version in a text message instead.
            if len(caption) <= 1000:
                await msg.reply_text(caption)
            else:
                await msg.reply_text("📷 Анализ завершён. Основные параметры — в отчётах ниже.")

            txt_path = tmp_path / "report.txt"
            json_path = tmp_path / "metadata.json"
            txt_path.write_text(
                render_txt(result) + f"\nSHA-256 файла: {sha256}\n"
                f"Получено как сжатая Telegram-фотография: {'да' if was_photo else 'нет'}\n",
                encoding="utf-8",
            )
            import json
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

async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Пришли фотографию или изображение как файл. Для полного EXIF лучше использовать «Отправить как файл»."
        )

def main() -> None:
    if not TOKEN:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN. Скопируй .env.example в .env и укажи токен.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, process_image))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, unsupported))
    log.info("Bot starting")

    base_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if base_url:
        # Webhook mode (Render free web service): bind $PORT, health on "/".
        port = int(os.getenv("PORT", "10000"))
        secret = os.getenv("WEBHOOK_SECRET", TOKEN[-16:])
        run = app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=secret,
            webhook_url=f"{base_url.rstrip('/')}/{secret}",
            allowed_updates=Update.ALL_TYPES,
        )
        log.info("Webhook mode on port %d", port)
        return run
    log.info("Polling mode")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
