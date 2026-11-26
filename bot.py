
import os
import logging
import re
import json

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
delegated_admins = {}
known_users = {}

PERMISSIONS_FILE = "paradox_permissions.json"

BAD_WORDS = [
    "كلب",
    "حمار",
    "غبي",
    "حقير",
    "تافه"
]


def load_permissions():
    global delegated_admins
    try:
        with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            delegated_admins = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        delegated_admins = {}


def save_permissions():
    with open(PERMISSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(delegated_admins, f, ensure_ascii=False)


def contains_link(text):
    return bool(re.search(
        r"(https?://|www\.|t\.me/|telegram\.me/)",
        text,
        re.IGNORECASE
    ))


def is_delegated(chat_id, user_id):
    return str(user_id) in delegated_admins.get(str(chat_id), [])


async def is_owner(chat, user_id):
    member = await chat.get_member(user_id)
    return member.status == "creator"


async def can_unmute(chat, user_id):
    member = await chat.get_member(user_id)

    if member.status == "creator":
        return True

    if member.status != "administrator":
        return False

    return is_delegated(chat.id, user_id)


async def find_target(chat_id, username):
    username = username.lstrip("@").lower()

    for user_id, user in known_users.get(chat_id, {}).items():
        if user.username and user.username.lower() == username:
            return user

    return None


async def grant_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    owner = update.effective_user

    if not message or not chat or not owner:
        return

    if not await is_owner(chat, owner.id):
        await message.reply_text("⛔ المالك فقط يگدر يمنح صلاحية فك الكتم.")
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("⚠️ رد على رسالة المشرف واكتب: منح فك الكتم")
        return

    target = message.reply_to_message.from_user

    member = await chat.get_member(target.id)

    if member.status != "administrator":
        await message.reply_text("⚠️ هذا العضو مو مشرف بالمجموعة.")
        return

    chat_key = str(chat.id)
    delegated_admins.setdefault(chat_key, [])

    if str(target.id) not in delegated_admins[chat_key]:
        delegated_admins[chat_key].append(str(target.id))
        save_permissions()

    await message.reply_text(
        f"✅ تم منح {target.first_name} صلاحية فك الكتم."
    )


async def revoke_permission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    owner = update.effective_user

    if not message or not chat or not owner:
        return

    if not await is_owner(chat, owner.id):
        await message.reply_text("⛔ المالك فقط يگدر يسحب الصلاحية.")
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("⚠️ رد على رسالة المشرف واكتب: سحب فك الكتم")
        return

    target = message.reply_to_message.from_user

    chat_key = str(chat.id)
    delegated_admins.setdefault(chat_key, [])

    if str(target.id) in delegated_admins[chat_key]:
        delegated_admins[chat_key].remove(str(target.id))
        save_permissions()

    await message.reply_text(
        f"🔒 تم سحب صلاحية فك الكتم من {target.first_name}."
    )


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    if not await can_unmute(chat, user.id):
        await message.reply_text("⛔ ما عندك صلاحية فك الكتم.")
        return

    target = None

    if message.reply_to_message:
        target = message.reply_to_message.from_user

    elif context.args:
        target = await find_target(chat.id, context.args[0])

    if not target:
        await message.reply_text(
            "⚠️ رد على رسالة العضو واكتب فتح الكتم، "
            "أو استخدم /unmute @username"
        )
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
            f"🔓 تم فك الكتم عن {target.first_name}."
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

    known_users.setdefault(chat.id, {})[user.id] = user

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

    if count == WARN_LIMIT:
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
                f"🔇 تم كتم {user.first_name} بعد وصوله إلى 4 إنذارات."
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


async def text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or not message.text:
        return

    command = message.text.strip()

    if command == "منح فك الكتم":
        await grant_permission(update, context)

    elif command == "سحب فك الكتم":
        await revoke_permission(update, context)

    elif command == "فتح الكتم":
        await unmute(update, context)

    elif command.startswith("فتح الكتم @"):
        username = command.split("@", 1)[1].strip()
        context.args = [f"@{username}"]
        await unmute(update, context)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    load_permissions()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("unmute", unmute))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_commands
        )
    )

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            moderate
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
