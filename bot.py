
import os
import re
import json
import logging
import tempfile

from telegram import Update, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

WARN_LIMIT = 4
DATA_FILE = "paradox_data.json"

BAD_WORDS = [
    "كلب",
    "حمار",
    "غبي",
    "حقير",
    "تافه",
]

warnings = {}
delegated_admins = {}
known_users = {}


# =========================
# حفظ وتحميل البيانات
# =========================

def save_data():
    data = {
        "warnings": warnings,
        "delegated_admins": delegated_admins,
        "known_users": known_users,
    }

    directory = os.path.dirname(os.path.abspath(DATA_FILE))

    try:
        fd, temp_path = tempfile.mkstemp(
            dir=directory,
            suffix=".tmp"
        )

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        os.replace(temp_path, DATA_FILE)

    except Exception:
        logging.exception("Could not save data")


def load_data():
    global warnings, delegated_admins, known_users

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        warnings = data.get("warnings", {})
        delegated_admins = data.get("delegated_admins", {})
        known_users = data.get("known_users", {})

    except FileNotFoundError:
        logging.info("No saved data found. Starting fresh.")

    except Exception:
        logging.exception("Could not load saved data")


# =========================
# أدوات مساعدة
# =========================

def contains_link(text):
    pattern = (
        r"(https?://|www\.|t\.me/|telegram\.me/)"
    )

    return bool(
        re.search(pattern, text, re.IGNORECASE)
    )


def contains_bad_word(text):
    words = re.findall(r"\w+", text.lower())

    return any(
        word in BAD_WORDS
        for word in words
    )


def warning_key(chat_id, user_id):
    return f"{chat_id}:{user_id}"


def is_delegated(chat_id, user_id):
    admins = delegated_admins.get(str(chat_id), [])
    return str(user_id) in admins


async def is_owner(chat, user_id):
    try:
        member = await chat.get_member(user_id)
        return member.status == "creator"
    except Exception:
        logging.exception("Could not check owner")
        return False


async def can_unmute(chat, user_id):
    try:
        member = await chat.get_member(user_id)

        if member.status == "creator":
            return True

        if member.status != "administrator":
            return False

        return is_delegated(chat.id, user_id)

    except Exception:
        logging.exception("Could not check unmute permission")
        return False


def remember_user(chat_id, user):
    if not user:
        return

    chat_key = str(chat_id)

    known_users.setdefault(chat_key, {})

    known_users[chat_key][str(user.id)] = {
        "username": (user.username or "").lower(),
        "first_name": user.first_name or "العضو",
    }


def find_user_by_username(chat_id, username):
    username = username.lstrip("@").lower()

    users = known_users.get(str(chat_id), {})

    for user_id, user_data in users.items():
        if user_data.get("username") == username:
            return int(user_id), user_data.get("first_name", "العضو")

    return None, None


# =========================
# أمر البداية
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "🔥 أهلاً بك في PARADOX 404\n\n"
        "أنا بوت إدارة المجموعات.\n"
        "🛡️ أحذف الروابط والألفاظ المسيئة.\n"
        "⚠️ أعطي 4 إنذارات.\n"
        "🔇 أكتم المخالف بعد الإنذار الرابع.\n\n"
        "صلاحية فك الكتم للمالك والمشرفين المفوضين فقط."
    )


# =========================
# منح صلاحية فك الكتم
# =========================

async def grant_permission(update, context):
    message = update.effective_message
    chat = update.effective_chat
    owner = update.effective_user

    if not message or not chat or not owner:
        return

    if not await is_owner(chat, owner.id):
        await message.reply_text(
            "⛔ المالك فقط يگدر يمنح صلاحية فك الكتم."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ رد على رسالة المشرف واكتب:\n"
            "منح فك الكتم"
        )
        return

    target = message.reply_to_message.from_user

    if not target or target.is_bot:
        await message.reply_text("⚠️ العضو غير صالح.")
        return

    try:
        member = await chat.get_member(target.id)

        if member.status != "administrator":
            await message.reply_text(
                "⚠️ هذا العضو مو مشرف بالمجموعة."
            )
            return

    except Exception:
        await message.reply_text(
            "⚠️ تعذر التحقق من رتبة العضو."
        )
        return

    chat_key = str(chat.id)

    delegated_admins.setdefault(chat_key, [])

    if str(target.id) not in delegated_admins[chat_key]:
        delegated_admins[chat_key].append(str(target.id))

    save_data()

    await message.reply_text(
        f"✅ تم منح {target.first_name} صلاحية فك الكتم."
    )


# =========================
# سحب صلاحية فك الكتم
# =========================

async def revoke_permission(update, context):
    message = update.effective_message
    chat = update.effective_chat
    owner = update.effective_user

    if not message or not chat or not owner:
        return

    if not await is_owner(chat, owner.id):
        await message.reply_text(
            "⛔ المالك فقط يگدر يسحب الصلاحية."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "⚠️ رد على رسالة المشرف واكتب:\n"
            "سحب فك الكتم"
        )
        return

    target = message.reply_to_message.from_user

    if not target:
        return

    chat_key = str(chat.id)

    delegated_admins.setdefault(chat_key, [])

    if str(target.id) in delegated_admins[chat_key]:
        delegated_admins[chat_key].remove(str(target.id))

    save_data()

    await message.reply_text(
        f"🔒 تم سحب صلاحية فك الكتم من {target.first_name}."
    )


# =========================
# فك الكتم
# =========================

async def unmute_member(update, context, target_id, target_name):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    if not await can_unmute(chat, user.id):
        await message.reply_text(
            "⛔ ما عندك صلاحية فك الكتم."
        )
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target_id,
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
                can_add_web_page_previews=True,
            ),
        )

        key = warning_key(chat.id, target_id)
        warnings[key] = 0
        save_data()

        await message.reply_text(
            f"🔓 تم فك الكتم عن {target_name}."
        )

    except Exception:
        logging.exception("Could not unmute member")

        await message.reply_text(
            "⚠️ تعذر فك الكتم.\n"
            "تأكد أن البوت مشرف ويملك صلاحية تقييد الأعضاء."
        )


async def unmute(update, context):
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    target_id = None
    target_name = "العضو"

    if message.reply_to_message:
        target = message.reply_to_message.from_user

        if target:
            target_id = target.id
            target_name = target.first_name

    elif context.args:
        target_id, target_name = find_user_by_username(
            chat.id,
            context.args[0]
        )

    if not target_id:
        await message.reply_text(
            "⚠️ رد على رسالة العضو واكتب فتح الكتم.\n"
            "أو استخدم /unmute @username إذا كان البوت يعرفه."
        )
        return

    await unmute_member(
        update,
        context,
        target_id,
        target_name
    )


# =========================
# نظام التحذيرات والكتم
# =========================

async def moderate(update, context):
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or user.is_bot or not chat:
        return

    if chat.type not in ("group", "supergroup"):
        return

    try:
        member = await chat.get_member(user.id)

        if member.status in ("administrator", "creator"):
            return

    except Exception:
        logging.exception("Could not check member")
        return

    remember_user(chat.id, user)

    text = message.text or message.caption or ""

    reason = None

    if contains_link(text):
        reason = "نشر رابط ممنوع"

    elif contains_bad_word(text):
        reason = "استخدام ألفاظ مسيئة"

    if not reason:
        save_data()
        return

    try:
        await message.delete()

    except Exception:
        logging.exception("Could not delete message")
        return

    key = warning_key(chat.id, user.id)

    count = int(warnings.get(key, 0)) + 1
    warnings[key] = count

    save_data()

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
                    can_add_web_page_previews=False,
                ),
            )

            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    f"🔇 تم كتم {user.first_name}.\n"
                    f"وصل إلى {WARN_LIMIT} إنذارات.\n"
                    "لا يمكنه العودة إلا بعد فك الكتم من شخص مخوّل."
                ),
            )

        except Exception:
            logging.exception("Could not mute member")

            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "⚠️ تعذر كتم العضو.\n"
                    "تأكد من صلاحيات البوت."
                ),
            )

    else:
        await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"⚠️ تحذير {count}/{WARN_LIMIT}\n"
                f"العضو: {user.first_name}\n"
                f"السبب: {reason}"
            ),
        )


# =========================
# الأوامر العربية
# =========================

async def text_commands(update, context):
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

        target_id, target_name = find_user_by_username(
            update.effective_chat.id,
            username
        )

        if target_id:
            await unmute_member(
                update,
                context,
                target_id,
                target_name
            )
        else:
            await message.reply_text(
                "⚠️ ما لكيت هذا المستخدم ضمن الأعضاء المعروفين للبوت."
            )


# =========================
# تشغيل البوت
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing from Railway Variables"
        )

    load_data()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("unmute", unmute)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_commands
        ),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            moderate
        ),
        group=1,
    )

    logging.info("PARADOX 404 is starting...")

    app.run_polling()


if __name__ == "__main__":
    main()
