
import os
import re
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# قائمة الألفاظ المسيئة - قابلة للتعديل
BAD_WORDS = {
    "يا حمار",
    "كلب",
    "غبي",
}

# الروابط المسموحة
ALLOWED_DOMAINS = set()

# عدد التحذيرات لكل عضو داخل كل مجموعة
WARNINGS = {}

URL_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/)",
    re.IGNORECASE
)


async def is_admin(update, context):
    member = await context.bot.get_chat_member(
        update.effective_chat.id,
        update.effective_user.id
    )
    return member.status in ("administrator", "creator")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 أهلاً بك في PARADOX 404\n"
        "أنا بوت إدارة المجموعات الذكي."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 أوامر PARADOX 404\n\n"
        "/start - تشغيل البوت\n"
        "/help - المساعدة\n"
        "/id - معرف المجموعة\n"
        "/warns - عدد تحذيراتك"
    )


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"معرف المجموعة: {update.effective_chat.id}"
    )


async def show_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.effective_chat.id, update.effective_user.id)
    count = WARNINGS.get(key, 0)
    await update.message.reply_text(
        f"عدد تحذيراتك: {count}"
    )


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        await update.message.reply_text(
            f"أهلاً بك {member.first_name} في PARADOX 404 🔥"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not message.text or chat.type == "private":
        return

    # إعفاء مشرفي المجموعة
    if await is_admin(update, context):
        return

    text = message.text.lower()

    # فحص الألفاظ المسيئة
    offensive = any(word in text for word in BAD_WORDS)

    # فحص الروابط
    has_link = bool(URL_PATTERN.search(text))

    if not offensive and not has_link:
        if "مرحبا" in text or "السلام عليكم" in text:
            await message.reply_text(
                "وعليكم السلام ورحمة الله وبركاته ❤️"
            )
        return

    key = (chat.id, user.id)
    WARNINGS[key] = WARNINGS.get(key, 0) + 1
    count = WARNINGS[key]

    if offensive:
        reason = "استخدام ألفاظ مسيئة"
    else:
        reason = "نشر رابط غير مسموح"

    await message.reply_text(
        f"⚠️ تنبيه من PARADOX 404\n"
        f"العضو: {user.first_name}\n"
        f"السبب: {reason}\n"
        f"عدد التحذيرات: {count}\n"
        f"يرجى الالتزام بقوانين المجموعة."
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", get_id))
    app.add_handler(CommandHandler("warns", show_warns))

    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome)
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("PARADOX 404 is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
