
import os
import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import Update, ChatPermissions
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
MUTE_HOURS = 24

warnings = {}

BAD_WORDS = [
    "كلب",
    "حمار",
    "غبي",
    "حقير",
    "تافه"
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
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_audios=False,
                    can_send_documents=False,
                    can_send_photos=False,
                    can_send_videos=False,
                    can_send_video_notes=False,
                    can_send_voice_notes=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=datetime.now(timezone.utc)
                + timedelta(hours=MUTE_HOURS)
            )

            await context.bot.send_message(
                chat.id,
                f"🔇 تم كتم {user.first_name} لمدة 24 ساعة "
                "بعد وصوله إلى 4 إنذارات."
            )

            warnings[key] = 0

        except Exception:
            logging.exception("Could not mute member")
            await context.bot.send_message(
                chat.id,
                "⚠️ تعذر كتم العضو. تحقق من صلاحيات البوت."
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
