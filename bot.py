
import os
import logging
import re

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


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    owner = update.effective_user

    if not message or not owner or not chat:
        return

    member = await chat.get_member(owner.id)

    if member.status != "creator":
        await message.reply_text(
            "⛔ هذا الأمر مخصص لمالك المجموعة فقط."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ رد على رسالة العضو المكتوم واكتب /unmute"
        )
        return

    target = message.reply_to_message.from_user

    if not target:
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )

        warnings[(chat.id, target.id)] = 0

        await message.reply_text(
            f"🔓 تم فك الكتم عن {target.first_name} "
            "بواسطة مالك المجموعة."
        )

    except Exception:
        logging.exception("Could not unmute member")
        await message.reply_text(
            "⚠️ تعذر فك الكتم. تحقق من صلاحيات البوت."
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
                )
            )

            await context.bot.send_message(
                chat.id,
                f"🔇 تم كتم {user.first_name} نهائياً "
                "بعد وصوله إلى 4 إنذارات.\n"
                "🔐 فك الكتم متاح لمالك المجموعة فقط."
            )

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
    app.add_handler(CommandHandler("unmute", unmute))

    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.CAPTION,
            moderate
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
