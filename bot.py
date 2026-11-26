
import os
import logging
import re
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

WARN_LIMIT = 4
warnings = {}

BAD_WORDS = [
    "كلب", "حمار", "غبي", "حقير", "تافه"
]

def contains_link(text):
    return bool(re.search(
        r"(https?://|www\.|t\.me/|telegram\.me/)",
        text,
        re.IGNORECASE
    ))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 أهلاً بك في PARADOX 404\n"
        "أنا بوت إدارة المجموعات الذكي."
    )

async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or user.is_bot:
        return

    member = await chat.get_member(user.id)
    if member.status in ("administrator", "creator"):
        return

    text = message.text or message.caption or ""

    reason = None

    if contains_link(text):
        reason = "نشر رابط ممنوع"

    elif any(word in text.split() for word in BAD_WORDS):
        reason = "استخدام ألفاظ مسيئة"

    if not reason:
        return

    try:
        await message.delete()
    except Exception:
        logging.exception("Could not delete message")
        return

    key = (chat.id, user.id)
    warnings[key] = warnings.get(key, 0) + 1
    count = warnings[key]

    if count >= WARN_LIMIT:
        try:
            await chat.ban_member(user.id)
            await context.bot.send_message(
                chat.id,
                f"🚫 تم حظر {user.first_name} بعد {count} إنذارات."
            )
            warnings[key] = 0
        except Exception:
            logging.exception("Could not ban member")
            await context.bot.send_message(
                chat.id,
                f"⚠️ وصل {user.first_name} إلى {count} إنذارات، لكن تعذر حظره."
            )
    else:
        await context.bot.send_message(
            chat.id,
            f"⚠️ تحذير {count}/{WARN_LIMIT}\n"
            f"العضو: {user.first_name}\n"
            f"السبب: {reason}"
        )

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.CAPTION,
            moderate
        )
    )

    app.run_polling()

if __name__ == "__main__":
    main()
