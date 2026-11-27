
import os
import re
import json
import logging

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "paradox_data.json"
WARN_LIMIT = 4

BAD_WORDS = [
    "كلب",
    "حمار",
    "غبي",
    "حقير",
    "تافه",
]

LINK_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE,
)

# =========================
# تخزين البيانات
# =========================

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            result = json.load(f)
            if "chats" not in result:
                result["chats"] = {}
            return result
    except (FileNotFoundError, json.JSONDecodeError):
        return {"chats": {}}


data = load_data()


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def chat_data(chat_id):
    key = str(chat_id)

    if key not in data["chats"]:
        data["chats"][key] = {}

    info = data["chats"][key]

    info.setdefault("warnings", {})
    info.setdefault("muted", {})
    info.setdefault("delegates", [])
    info.setdefault("songs", {})

    return info


# =========================
# صلاحيات المالك والمشرف
# =========================

async def is_owner(chat_id, user_id, context):
    try:
        member = await context.bot.get_chat_member(
            chat_id, user_id
        )
        return member.status == "creator"
    except Exception:
        return False


async def is_admin(chat_id, user_id, context):
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

    info = chat_data(chat_id)

    if str(user_id) not in info["delegates"]:
        return False

    return await is_admin(chat_id, user_id, context)


# =========================
# الكتم وفك الكتم
# =========================

async def mute_member(chat_id, user_id, context):
    permissions = ChatPermissions(
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
    )

    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=permissions,
    )


async def unmute_member(chat_id, user_id, context):
    chat = await context.bot.get_chat(chat_id)
    permissions = chat.permissions

    if permissions is None:
        permissions = ChatPermissions(
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


# =========================
# قائمة البداية
# =========================

def main_menu():
    keyboard = [
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
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 أهلاً وسهلاً بك في PARADOX 404\n\n"
        "أنا مساعدك الذكي لإدارة المجموعات وحمايتها.\n"
        "موجود لخدمتكم وتنظيم المجموعة وتوفير أدوات "
        "مفيدة للأعضاء والمشرفين.\n\n"
        "اختار القسم اللي تريد تستكشفه:"
    )

    await update.effective_message.reply_text(
        text,
        reply_markup=main_menu(),
    )


async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.data == "management":
        text = (
            "🛡️ الإدارة والحماية\n\n"
            "ميزات الحماية:\n"
            "• حذف الروابط المخالفة.\n"
            "• حذف الألفاظ المسيئة.\n"
            "• نظام إنذارات للأعضاء.\n"
            "• كتم العضو بعد 4 إنذارات.\n\n"
            "أوامر الإدارة:\n"
            "• /unmute لفك كتم عضو بالرد على رسالته.\n"
            "• /grantunmute لمنح مشرف صلاحية فك الكتم.\n"
            "• /revokeunmute لسحب صلاحية فك الكتم.\n"
            "• /unmuteall لمسح المكتومين.\n\n"
            "يمكن للجميع مشاهدة هذا القسم، لكن تنفيذ "
            "الأوامر محمي بالصلاحيات."
        )

    elif query.data == "development":
        text = (
            "🚀 التطوير والإضافات\n\n"
            "الإضافات الحالية:\n"
            "• 🎵 البحث عن الأغاني بأمر: يوت اسم الأغنية.\n"
            "• 🤖 التفاعل مع اسم نيكسوس.\n\n"
            "التطويرات القادمة:\n"
            "• الذكاء الاصطناعي.\n"
            "• نظام ترحيب متطور.\n"
            "• إحصائيات المجموعة.\n"
            "• أدوات إضافية للإدارة."
        )
    else:
        return

    keyboard = [[
        InlineKeyboardButton(
            "⬅️ رجوع للقائمة",
            callback_data="home",
        )
    ]]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def home_callback(update, context):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🔥 أهلاً بك في PARADOX 404\n\nاختر القسم:",
        reply_markup=main_menu(),
    )


# =========================
# منح صلاحية فك الكتم
# =========================

async def grant_unmute(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not await is_owner(chat.id, user.id, context):
        await message.reply_text(
            "⛔ هذا الأمر خاص بمالك المجموعة."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة المشرف واكتب /grantunmute"
        )
        return

    target = message.reply_to_message.from_user

    if not await is_admin(chat.id, target.id, context):
        await message.reply_text(
            "⚠️ لازم يكون الشخص مشرفاً بالمجموعة أولاً."
        )
        return

    info = chat_data(chat.id)

    if str(target.id) not in info["delegates"]:
        info["delegates"].append(str(target.id))

    save_data()

    await message.reply_text(
        f"✅ تم منح {target.first_name} صلاحية فك الكتم."
    )


async def revoke_unmute(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not await is_owner(chat.id, user.id, context):
        await message.reply_text(
            "⛔ هذا الأمر خاص بمالك المجموعة."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة المشرف واكتب /revokeunmute"
        )
        return

    target = message.reply_to_message.from_user
    info = chat_data(chat.id)

    info["delegates"] = [
        item for item in info["delegates"]
        if item != str(target.id)
    ]

    save_data()

    await message.reply_text(
        f"✅ تم سحب صلاحية فك الكتم من {target.first_name}."
    )


# =========================
# فك كتم عضو
# =========================

async def unmute_command(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not await can_unmute(chat.id, user.id, context):
        await message.reply_text(
            "⛔ ما عندك صلاحية فك الكتم."
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "رد على رسالة العضو المكتوم واكتب /unmute"
        )
        return

    target = message.reply_to_message.from_user

    try:
        await unmute_member(chat.id, target.id, context)

        info = chat_data(chat.id)
        info["muted"].pop(str(target.id), None)
        info["warnings"].pop(str(target.id), None)
        save_data()

        await message.reply_text(
            f"🔊 تم فك الكتم عن {target.first_name}."
        )

    except Exception:
        logger.exception("Unmute error")
        await message.reply_text(
            "❌ تعذر فك الكتم. تأكد من صلاحيات البوت."
        )


# =========================
# مسح جميع المكتومين
# =========================

async def unmute_all(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not await is_owner(chat.id, user.id, context):
        await message.reply_text(
            "⛔ هذا الأمر خاص بمالك المجموعة."
        )
        return

    info = chat_data(chat.id)
    muted_ids = list(info["muted"].keys())

    if not muted_ids:
        await message.reply_text(
            "ℹ️ ماكو أعضاء مسجلين بالكتم عند البوت."
        )
        return

    success = 0
    failed = 0

    for user_id in muted_ids:
        try:
            await unmute_member(
                chat.id,
                int(user_id),
                context,
            )

            info["muted"].pop(user_id, None)
            info["warnings"].pop(user_id, None)
            success += 1

        except Exception as error:
            logger.warning(
                "Could not unmute %s: %s",
                user_id,
                error,
            )
            failed += 1

    save_data()

    await message.reply_text(
        "🧹 انتهت عملية مسح المكتومين.\n\n"
        f"✅ تم فك الكتم: {success}\n"
        f"❌ تعذر فك الكتم: {failed}"
    )


# =========================
# إضافة أغنية للمكتبة
# =========================

async def add_song(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not await is_owner(chat.id, user.id, context):
        await message.reply_text(
            "⛔ إضافة الأغاني خاصة بمالك المجموعة."
        )
        return

    if not context.args:
        await message.reply_text(
            "رد على ملف صوتي واكتب:\n/addsong اسم الأغنية"
        )
        return

    if not message.reply_to_message:
        await message.reply_text(
            "لازم ترد على رسالة تحتوي ملفاً صوتياً."
        )
        return

    replied = message.reply_to_message
    audio = replied.audio

    if audio is None and replied.document:
        if (
            replied.document.mime_type
            and replied.document.mime_type.startswith("audio/")
        ):
            audio = replied.document

    if audio is None:
        await message.reply_text(
            "❌ الرسالة ما تحتوي ملفاً صوتياً."
        )
        return

    name = " ".join(context.args).strip()
    info = chat_data(chat.id)

    info["songs"][name.lower()] = {
        "name": name,
        "file_id": audio.file_id,
    }

    save_data()

    await message.reply_text(
        f"✅ تمت إضافة الأغنية:\n🎵 {name}"
    )


async def delete_song(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not await is_owner(chat.id, user.id, context):
        await message.reply_text(
            "⛔ هذا الأمر خاص بمالك المجموعة."
        )
        return

    if not context.args:
        await message.reply_text(
            "اكتب: /delsong اسم الأغنية"
        )
        return

    name = " ".join(context.args).strip().lower()
    info = chat_data(chat.id)

    if name not in info["songs"]:
        await message.reply_text(
            "❌ الأغنية غير موجودة بالمكتبة."
        )
        return

    del info["songs"][name]
    save_data()

    await message.reply_text(
        "🗑️ تم حذف الأغنية من المكتبة."
    )


# =========================
# البحث عن أغنية
# =========================

async def search_song(message, chat_id, query):
    info = chat_data(chat_id)
    songs = info["songs"]

    query = query.strip().lower()

    if not query:
        await message.reply_text(
            "اكتب اسم الأغنية بعد كلمة يوت."
        )
        return

    results = [
        song for key, song in songs.items()
        if query in key or key in query
    ]

    if not results:
        await message.reply_text(
            "🔎 ما لقيت الأغنية بمكتبة البوت.\n"
            "مالك المجموعة يگدر يضيفها للمكتبة "
            "إذا كانت مسموحة بالمشاركة."
        )
        return

    song = results[0]

    try:
        await message.reply_audio(
            audio=song["file_id"],
            title=song["name"],
            caption=f"🎵 {song['name']}\n"
                    "بواسطة PARADOX 404",
        )
    except Exception:
        logger.exception("Song sending error")
        await message.reply_text(
            "❌ تعذر إرسال الملف الصوتي."
        )


# =========================
# التفاعل مع اسم نيكسوس
# =========================

async def nexus_reply(message):
    await message.reply_text(
        "هلا بيك، نيكسوس موجود لخدمتكم 🔥🤖"
    )


# =========================
# مراقبة رسائل المجموعة
# =========================

async def moderate(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not user:
        return

    if chat.type not in ("group", "supergroup"):
        return

    if user.is_bot:
        return

    text = message.text or message.caption or ""

    if not text:
        return

    # أمر مسح المكتومين بالعربي
    if text.strip() == "مسح المكتومين":
        await unmute_all(update, context)
        return

    # البحث عن الأغاني متاح للجميع
    song_match = re.match(
        r"^\s*يوت\s+(.+)$",
        text,
        re.IGNORECASE,
    )

    if song_match:
        await search_song(
            message,
            chat.id,
            song_match.group(1),
        )
        return

    # التفاعل مع اسم نيكسوس متاح للجميع
    if re.search(r"نيكسوس|نكسوس", text):
        await nexus_reply(message)
        return

    # لا نطبق الحماية على المشرفين
    if await is_admin(chat.id, user.id, context):
        return

    info = chat_data(chat.id)
    user_key = str(user.id)

    # حذف الروابط
    if LINK_PATTERN.search(text):
        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete link")

        await message.reply_text(
            f"⚠️ {user.first_name}، الروابط ممنوعة بالمجموعة."
        )
        return

    # حذف الألفاظ المسيئة
    normalized = text.lower()

    if any(word in normalized for word in BAD_WORDS):
        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete bad message")

        warnings = info["warnings"]
        warnings[user_key] = min(
            int(warnings.get(user_key, 0)) + 1,
            WARN_LIMIT,
        )

        count = warnings[user_key]
        save_data()

        if count >= WARN_LIMIT:
            if user_key in info["muted"]:
                return

            try:
                await mute_member(chat.id, user.id, context)

                info["muted"][user_key] = {
                    "name": user.full_name,
                }
                save_data()

                await message.reply_text(
                    f"🔇 تم كتم {user.first_name} "
                    f"بعد وصوله إلى {WARN_LIMIT} إنذارات."
                )

            except Exception:
                logger.exception("Mute error")
                await message.reply_text(
                    "❌ تعذر كتم العضو. تأكد من صلاحيات البوت."
                )

        else:
            await message.reply_text(
                f"⚠️ تحذير {count} من {WARN_LIMIT} "
                f"لـ {user.first_name}."
            )


# =========================
# تشغيل البوت
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود في متغيرات Railway."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        CallbackQueryHandler(
            menu_callback,
            pattern="^(management|development)$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            home_callback,
            pattern="^home$",
        )
    )

    app.add_handler(CommandHandler("grantunmute", grant_unmute))
    app.add_handler(CommandHandler("revokeunmute", revoke_unmute))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("unmuteall", unmute_all))

    app.add_handler(CommandHandler("addsong", add_song))
    app.add_handler(CommandHandler("delsong", delete_song))

    app.add_handler(
        MessageHandler(
            (filters.TEXT & ~filters.COMMAND) | filters.CAPTION,
            moderate,
        )
    )

    logger.info("PARADOX 404 is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
