
import os
import re
import json
import logging
import tempfile

from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "paradox_data.json"
WARN_LIMIT = 4

BAD_WORDS = ["كلب", "حمار", "غبي", "حقير", "تافه"]

data = {
    "warnings": {},
    "delegated_admins": {},
    "known_users": {},
    "muted_members": {},
    "roles": {},
}

ROLE_NAMES = {
    "owner": "المالك",
    "super_admin": "المشرف العام",
    "admin": "المشرف",
    "moderator": "المراقب",
    "member": "العضو",
}


# =====================================
# حفظ وتحميل البيانات
# =====================================

def load_data():
    global data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)

        for key in data:
            if key in saved:
                data[key] = saved[key]

    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Data loading error")


def save_data():
    temp_path = None

    try:
        folder = os.path.dirname(os.path.abspath(DATA_FILE))
        fd, temp_path = tempfile.mkstemp(dir=folder)

        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

        os.replace(temp_path, DATA_FILE)

    except Exception:
        logging.exception("Data saving error")

        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def ck(chat_id):
    return str(chat_id)


def uk(user_id):
    return str(user_id)


# =====================================
# التحقق من المالك والمشرفين
# =====================================

async def is_owner(chat_id, user_id, context):
    try:
        member = await context.bot.get_chat_member(
            chat_id, user_id
        )
        return member.status == "creator"
    except Exception:
        return False


async def is_real_admin(chat_id, user_id, context):
    try:
        member = await context.bot.get_chat_member(
            chat_id, user_id
        )
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def can_unmute(chat_id, user_id, context):
    if await is_owner(chat_id, user_id, context):
        return True

    if not await is_real_admin(chat_id, user_id, context):
        return False

    admins = data["delegated_admins"].get(ck(chat_id), [])
    return uk(user_id) in admins


def get_role(chat_id, user_id):
    return data["roles"].get(ck(chat_id), {}).get(
        uk(user_id), "member"
    )


# =====================================
# حفظ معلومات الأعضاء
# =====================================

def remember_user(chat_id, user):
    if not user:
        return

    data["known_users"].setdefault(ck(chat_id), {})[uk(user.id)] = {
        "id": user.id,
        "name": user.full_name,
        "username": user.username or "",
    }


def find_username(chat_id, username):
    username = username.replace("@", "").lower()

    users = data["known_users"].get(ck(chat_id), {})

    for uid, info in users.items():
        if info.get("username", "").lower() == username:
            return int(uid)

    return None


# =====================================
# واجهة الترحيب
# =====================================

def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛡️ الإدارة والحماية",
                callback_data="management",
            )
        ],
        [
            InlineKeyboardButton(
                "🚀 التطوير والإضافات",
                callback_data="development",
            )
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    await message.reply_text(
        "🔥 أهلاً وسهلاً بك في PARADOX 404\n\n"
        "أنا مساعدك الذكي لإدارة المجموعات وحمايتها.\n\n"
        "نظام متطور صُمم لتوفير بيئة منظمة وآمنة "
        "وتجربة إدارة أكثر ذكاءً.\n\n"
        "اختر القسم الذي تريد الدخول إليه:",
        reply_markup=home_keyboard(),
    )


# =====================================
# القوائم الداخلية
# =====================================

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    chat = query.message.chat

    if query.data == "home":
        await query.edit_message_text(
            "🔥 أهلاً بك في PARADOX 404\n\n"
            "اختر القسم الذي تريد الدخول إليه:",
            reply_markup=home_keyboard(),
        )
        return

    if query.data == "development":
        await query.edit_message_text(
            "🚀 التطوير والإضافات المستقبلية\n\n"
            "هذا القسم مستقل عن الإدارة والحماية.\n"
            "ستضاف هنا الميزات الجديدة مستقبلاً.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ رجوع",
                        callback_data="home",
                    )
                ]
            ]),
        )
        return

    if query.data == "management":
        if chat.type not in ("group", "supergroup"):
            await query.edit_message_text(
                "افتح قسم الإدارة من داخل المجموعة "
                "حتى نتحقق من رتبتك."
            )
            return

        if await is_owner(chat.id, user.id, context):
            role = "primary_owner"
        else:
            role = get_role(chat.id, user.id)

        if role == "member":
            await query.edit_message_text(
                "⛔ هذا القسم مخصص للإدارة فقط.\n\n"
                "لا تملك صلاحية الدخول.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ رجوع",
                            callback_data="home",
                        )
                    ]
                ]),
            )
            return

        if role == "moderator":
            text = (
                "👁️ قسم المراقبة\n\n"
                "رتبتك: المراقب\n"
                "متابعة المخالفات والحماية."
            )

        elif role == "primary_owner":
            text = (
                "👑 الإدارة والحماية\n\n"
                "رتبتك: المالك الأساسي\n\n"
                "أوامرك:\n"
                "• تعيين مالك\n"
                "• تعيين مشرف عام\n"
                "• تعيين مشرف\n"
                "• تعيين مراقب\n"
                "• إزالة الرتبة\n"
                "• منح فك الكتم\n"
                "• سحب فك الكتم\n"
                "• فتح الكتم\n"
                "• مسح المكتومين"
            )

        else:
            text = (
                "🛡️ الإدارة والحماية\n\n"
                f"رتبتك: {ROLE_NAMES.get(role, 'عضو')}\n\n"
                "الصلاحيات:\n"
                "متابعة إدارة المجموعة حسب رتبتك."
            )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ رجوع",
                        callback_data="home",
                    )
                ]
            ]),
        )


# =====================================
# تعيين الرتب
# =====================================

async def assign_role(update, context, role):
    message = update.effective_message
    chat = update.effective_chat
    actor = update.effective_user

    if not await is_owner(chat.id, actor.id, context):
        await message.reply_text(
            "⛔ المالك الأساسي فقط يستطيع توزيع الرتب."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة الشخص المطلوب تعيين رتبته."
        )
        return

    target = message.reply_to_message.from_user

    if not target or target.is_bot or target.id == actor.id:
        await message.reply_text("⚠️ الشخص غير صالح.")
        return

    if role in ("owner", "super_admin", "admin"):
        if not await is_real_admin(chat.id, target.id, context):
            await message.reply_text(
                "يجب أن يكون الشخص مشرفاً فعلياً بالمجموعة أولاً."
            )
            return

    data["roles"].setdefault(ck(chat.id), {})[uk(target.id)] = role
    remember_user(chat.id, target)
    save_data()

    await message.reply_text(
        f"✅ تم تعيين {target.full_name}\n"
        f"الرتبة: {ROLE_NAMES[role]}"
    )


async def remove_role(update, context):
    message = update.effective_message
    chat = update.effective_chat

    if not await is_owner(chat.id, update.effective_user.id, context):
        await message.reply_text(
            "⛔ المالك الأساسي فقط يستطيع إزالة الرتب."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة الشخص المطلوب."
        )
        return

    target = message.reply_to_message.from_user

    data["roles"].setdefault(ck(chat.id), {}).pop(
        uk(target.id), None
    )

    data["delegated_admins"].setdefault(ck(chat.id), [])
    if uk(target.id) in data["delegated_admins"][ck(chat.id)]:
        data["delegated_admins"][ck(chat.id)].remove(uk(target.id))

    save_data()
    await message.reply_text("✅ تمت إزالة الرتبة والصلاحية الداخلية.")


# =====================================
# منح وسحب صلاحية فك الكتم
# =====================================

async def grant_permission(update, context):
    message = update.effective_message
    chat = update.effective_chat

    if not await is_owner(chat.id, update.effective_user.id, context):
        await message.reply_text(
            "⛔ المالك الأساسي فقط يستطيع منح الصلاحية."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة المشرف واكتب: منح فك الكتم"
        )
        return

    target = message.reply_to_message.from_user

    if not await is_real_admin(chat.id, target.id, context):
        await message.reply_text(
            "يجب أن يكون الشخص مشرفاً فعلياً."
        )
        return

    admins = data["delegated_admins"].setdefault(ck(chat.id), [])

    if uk(target.id) not in admins:
        admins.append(uk(target.id))

    save_data()
    await message.reply_text(
        f"✅ تم منح {target.full_name} صلاحية فك الكتم."
    )


async def revoke_permission(update, context):
    message = update.effective_message
    chat = update.effective_chat

    if not await is_owner(chat.id, update.effective_user.id, context):
        await message.reply_text(
            "⛔ المالك الأساسي فقط يستطيع سحب الصلاحية."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة المشرف واكتب: سحب فك الكتم"
        )
        return

    target = message.reply_to_message.from_user
    admins = data["delegated_admins"].setdefault(ck(chat.id), [])

    if uk(target.id) in admins:
        admins.remove(uk(target.id))

    save_data()
    await message.reply_text(
        f"🔒 تم سحب صلاحية فك الكتم من {target.full_name}."
    )


# =====================================
# فك الكتم
# =====================================

async def unmute_member(chat_id, user_id, context):
    chat = await context.bot.get_chat(chat_id)

    permissions = chat.permissions or ChatPermissions(
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
    )

    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=permissions,
    )


async def handle_unmute(update, context):
    message = update.effective_message
    chat = update.effective_chat
    actor = update.effective_user

    if not await can_unmute(chat.id, actor.id, context):
        await message.reply_text(
            "⛔ ما عندك صلاحية فتح الكتم."
        )
        return

    target_id = None

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id

    elif context.args:
        target_id = find_username(chat.id, context.args[0])

    if not target_id:
        await message.reply_text(
            "رد على رسالة العضو المكتوم، أو استخدم:\n"
            "/unmute @username"
        )
        return

    try:
        await unmute_member(chat.id, target_id, context)

        data["warnings"].setdefault(ck(chat.id), {}).pop(
            uk(target_id), None
        )

        data["muted_members"].setdefault(ck(chat.id), {}).pop(
            uk(target_id), None
        )

        save_data()
        await message.reply_text("✅ تم فتح الكتم بنجاح.")

    except Exception:
        logging.exception("Unmute failed")
        await message.reply_text(
            "تعذر فتح الكتم. تأكد من صلاحيات البوت."
        )


# =====================================
# مسح جميع المكتومين المسجلين
# =====================================

async def clear_muted(update, context):
    message = update.effective_message
    chat = update.effective_chat

    if not await is_owner(chat.id, update.effective_user.id, context):
        await message.reply_text(
            "⛔ هذا الأمر للمالك الأساسي فقط."
        )
        return

    chat_id = ck(chat.id)
    muted = data["muted_members"].setdefault(chat_id, {})

    if not muted:
        await message.reply_text(
            "لا يوجد أعضاء مكتومون مسجلون عند البوت."
        )
        return

    success = 0
    failed = 0

    for uid in list(muted.keys()):
        try:
            await unmute_member(chat.id, int(uid), context)

            data["warnings"].setdefault(chat_id, {}).pop(uid, None)
            muted.pop(uid, None)
            success += 1

        except Exception:
            failed += 1
            logging.exception("Bulk unmute failed")

    save_data()

    await message.reply_text(
        "🧹 تقرير مسح المكتومين\n\n"
        f"✅ تم فتح الكتم: {success}\n"
        f"❌ تعذر فتح الكتم: {failed}\n\n"
        "ملاحظة: البوت يستطيع فتح كتم الأعضاء "
        "الذين سجلهم بنفسه، وليس كل مكتوم قديم بالمجموعة."
    )


# =====================================
# استقبال الأوامر العربية
# =====================================

async def text_commands(update, context):
    message = update.effective_message
    chat = update.effective_chat

    if not message or not message.text:
        return

    if chat.type not in ("group", "supergroup"):
        return

    text = message.text.strip()

    if text == "منح فك الكتم":
        await grant_permission(update, context)
        return

    if text == "سحب فك الكتم":
        await revoke_permission(update, context)
        return

    if text == "مسح المكتومين":
        await clear_muted(update, context)
        return

    if text == "فتح الكتم":
        await handle_unmute(update, context)
        return

    if text.startswith("فتح الكتم @"):
        username = text.split("@", 1)[1].strip()
        context.args = [username]
        await handle_unmute(update, context)
        return

    role_commands = {
        "تعيين مالك": "owner",
        "تعيين مشرف عام": "super_admin",
        "تعيين مشرف": "admin",
        "تعيين مراقب": "moderator",
    }

    if text in role_commands:
        await assign_role(update, context, role_commands[text])
        return

    if text == "إزالة الرتبة":
        await remove_role(update, context)
        return


# =====================================
# نظام الحماية والإنذارات والكتم
# =====================================

async def moderate(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not user:
        return

    if chat.type not in ("group", "supergroup"):
        return

    remember_user(chat.id, user)

    text = message.text or message.caption or ""

    if not text:
        save_data()
        return

    try:
        member = await context.bot.get_chat_member(
            chat.id, user.id
        )

        if member.status in ("administrator", "creator"):
            save_data()
            return

    except Exception:
        return

    has_link = bool(re.search(
        r"(https?://|www\.|t\.me/|telegram\.me/)",
        text,
        re.IGNORECASE,
    ))

    has_bad_word = any(word in text for word in BAD_WORDS)

    if not has_link and not has_bad_word:
        save_data()
        return

    try:
        await message.delete()
    except Exception:
        logging.exception("Delete failed")

    chat_id = ck(chat.id)
    uid = uk(user.id)

    warnings = data["warnings"].setdefault(chat_id, {})
    muted = data["muted_members"].setdefault(chat_id, {})

    if uid in muted:
        save_data()
        return

    count = int(warnings.get(uid, 0)) + 1
    warnings[uid] = count

    if count >= WARN_LIMIT:
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=ChatPermissions(
                    can_send_messages=False
                ),
            )

            muted[uid] = {
                "id": user.id,
                "name": user.full_name,
                "username": user.username or "",
            }

            save_data()

            await context.bot.send_message(
                chat.id,
                f"🔇 تم كتم {user.full_name} بعد "
                f"{WARN_LIMIT} إنذارات."
            )

        except Exception:
            logging.exception("Mute failed")
            await context.bot.send_message(
                chat.id,
                "تعذر كتم العضو. تأكد من صلاحيات البوت."
            )

    else:
        save_data()

        await context.bot.send_message(
            chat.id,
            f"⚠️ تحذير {count}/{WARN_LIMIT}\n"
            f"العضو: {user.full_name}\n"
            "يرجى الالتزام بقوانين المجموعة."
        )


# =====================================
# معالجة الأخطاء
# =====================================

async def error_handler(update, context):
    logging.error(
        "Update error: %s",
        context.error,
    )


# =====================================
# تشغيل البوت
# =====================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود في متغيرات Railway."
        )

    load_data()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("unmute", handle_unmute))

    app.add_handler(
        CallbackQueryHandler(menu_callback)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_commands,
        ),
        group=0,
    )

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            moderate,
        ),
        group=1,
    )

    app.add_error_handler(error_handler)

    logging.info("PARADOX 404 started successfully")

    app.run_polling()


if __name__ == "__main__":
    main()
