import os
import re
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# PARADOX 404 - Telegram group manager
# Python 3.11+ / python-telegram-bot 21.x
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PARADOX_404")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "").strip()
DATA_FILE = Path(os.getenv("DATA_FILE", "paradox_data.json"))
WARN_LIMIT = 4

BAD_WORDS = ["كلب", "حمار", "غبي", "حقير", "تافه"]
LINK_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE,
)
SONG_PATTERN = re.compile(r"^\s*يوت\s+(.+)$", re.IGNORECASE)

DEFAULT_DATA = {
    "chats": {},
    "settings": {
        "force_channel": None,
        "force_channel_url": None,
    },
}


def load_data() -> dict:
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            raise ValueError("Invalid data file")
        loaded.setdefault("chats", {})
        loaded.setdefault("settings", {})
        loaded["settings"].setdefault("force_channel", None)
        loaded["settings"].setdefault("force_channel_url", None)
        return loaded
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULT_DATA))
    except (json.JSONDecodeError, ValueError):
        logger.exception("Data file is damaged; starting with empty data")
        return json.loads(json.dumps(DEFAULT_DATA))


data = load_data()


def save_data() -> None:
    """Atomic save to reduce the chance of a damaged JSON file."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, DATA_FILE)


def chat_data(chat_id: int) -> dict:
    key = str(chat_id)
    info = data["chats"].setdefault(key, {})
    info.setdefault("warnings", {})
    info.setdefault("muted", {})
    info.setdefault("banned", {})
    info.setdefault("restricted", {})
    info.setdefault("delegates", [])
    info.setdefault("songs", {})
    info.setdefault("activity", {})
    return info


def is_bot_owner(user_id: int) -> bool:
    return bool(BOT_OWNER_ID) and str(user_id) == BOT_OWNER_ID


async def is_group_owner(chat_id: int, user_id: int, context) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status == "creator"
    except TelegramError:
        return False


async def is_admin(chat_id: int, user_id: int, context) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False


async def can_unmute(chat_id: int, user_id: int, context) -> bool:
    if await is_group_owner(chat_id, user_id, context):
        return True
    info = chat_data(chat_id)
    return (
        str(user_id) in info["delegates"]
        and await is_admin(chat_id, user_id, context)
    )


def full_permissions() -> ChatPermissions:
    return ChatPermissions(
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
        can_invite_users=True,
        can_pin_messages=True,
        can_change_info=True,
        can_manage_topics=True,
    )


async def mute_member(chat_id: int, user_id: int, context) -> None:
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
        use_independent_chat_permissions=True,
    )


async def unmute_member(chat_id: int, user_id: int, context) -> None:
    chat = await context.bot.get_chat(chat_id)
    permissions = chat.permissions or full_permissions()
    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=permissions,
        use_independent_chat_permissions=True,
    )


async def force_subscription_ok(user_id: int, context) -> bool:
    channel = data["settings"].get("force_channel")
    if not channel:
        return True
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ("creator", "administrator", "member")
    except TelegramError:
        return False


def force_keyboard() -> InlineKeyboardMarkup:
    url = data["settings"].get("force_channel_url")
    channel = data["settings"].get("force_channel")
    if not url and channel:
        url = "https://t.me/" + str(channel).lstrip("@")
    rows = []
    if url:
        rows.append([InlineKeyboardButton("📢 اشترك بالقناة", url=url)])
    rows.append([InlineKeyboardButton("✅ تحقّق من الاشتراك", callback_data="check_subscription")])
    return InlineKeyboardMarkup(rows)


async def send_subscription_gate(message, context) -> None:
    await message.reply_text(
        "🔒 الاشتراك مطلوب\n\n"
        "حتى تستخدم خدمات PARADOX 404 داخل البوت، اشترك بقناة المالك "
        "ثم اضغط «تحقّق من الاشتراك».\n\n"
        "ملاحظة: هذا الشرط يخص استخدام البوت بالخاص فقط، ولا يمنع "
        "طلبات الأغاني أو الحديث أو أوامر المجموعة.",
        reply_markup=force_keyboard(),
    )


# =========================================================
# Private start and owner-only subscription settings
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if message.chat.type == ChatType.PRIVATE:
        if not is_bot_owner(user.id) and not await force_subscription_ok(user.id, context):
            await send_subscription_gate(message, context)
            return
        await message.reply_text(
            "🔥 أهلاً وسهلاً بك في PARADOX 404\n\n"
            "أنا نيكسوس، مساعدك لإدارة المجموعات وحمايتها.\n"
            "استخدم الأزرار لاستكشاف الخدمات.",
            reply_markup=main_menu(),
        )
    else:
        await message.reply_text(
            "🔥 PARADOX 404 موجود لخدمتكم.\n"
            "لإعدادات البوت افتح المحادثة الخاصة واضغط /start."
        )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ الإدارة والحماية", callback_data="management")],
        [InlineKeyboardButton("📊 الإحصائيات والقوائم", callback_data="statistics")],
        [InlineKeyboardButton("🎵 طريقة طلب الأغاني", callback_data="songs_help")],
    ])


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == "check_subscription":
        if is_bot_owner(user.id) or await force_subscription_ok(user.id, context):
            await query.edit_message_text(
                "✅ تم التحقق من اشتراكك.\n"
                "أهلاً بك في PARADOX 404!",
                reply_markup=main_menu(),
            )
        else:
            await query.answer(
                "ما ظهر اشتراكك بعد. اشترك بالقناة ثم حاول مرة ثانية.",
                show_alert=True,
            )
        return

    if query.message and query.message.chat.type == ChatType.PRIVATE:
        if not is_bot_owner(user.id) and not await force_subscription_ok(user.id, context):
            await query.answer("اشترك بالقناة أولاً.", show_alert=True)
            return

    texts = {
        "management": (
            "🛡️ الإدارة والحماية\n\n"
            "• حذف الروابط والألفاظ المسيئة.\n"
            "• أربعة إنذارات ثم كتم، دون حظر تلقائي.\n"
            "• المالك يمنح ويسحب صلاحية فك الكتم.\n"
            "• أوامر الإدارة داخل المجموعة تُنفذ حسب الصلاحية.\n\n"
            "الأوامر العربية: «المشرفون»، «المالكين»، «المكتومين»، "
            "«المحظورين»، «المقيدين»، «المتفاعلين»."
        ),
        "statistics": (
            "📊 الإحصائيات والقوائم\n\n"
            "اكتب داخل المجموعة إحدى الكلمات التالية:\n"
            "• المتفاعلين\n• المشرفون\n• المالكين الأساسيين\n"
            "• المكتومين\n• المحظورين\n• المقيدين"
        ),
        "songs_help": (
            "🎵 طلب الأغاني\n\n"
            "اكتب داخل المجموعة:\n"
            "يوت اسم الأغنية\n\n"
            "إذا كانت الأغنية محفوظة في مكتبة البوت، يرسلها نيكسوس "
            "داخل المجموعة كرد على طلبك.\n\n"
            "إضافة ملف صوتي للمكتبة: رد على الملف واكتب "
            "/addsong اسم الأغنية (للمالك فقط)."
        ),
    }
    if query.data == "home":
        await query.edit_message_text("🔥 PARADOX 404\nاختار القسم:", reply_markup=main_menu())
    elif query.data in texts:
        await query.edit_message_text(
            texts[query.data],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ رجوع", callback_data="home")]
            ]),
        )


# =========================================================
# Bot owner subscription controls (private chat only)
# =========================================================

async def set_force_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_bot_owner(user.id):
        await message.reply_text("⛔ هذا الإعداد خاص بمالك البوت الأساسي.")
        return
    if not context.args:
        await message.reply_text(
            "استخدم:\n/اشتراك_تفعيل @قناتك\n\n"
            "وتأكد أن البوت مشرف بالقناة."
        )
        return
    channel = context.args[0].strip()
    if not channel.startswith("@"):
        await message.reply_text("اكتب معرف القناة ويبدأ بـ @.")
        return
    data["settings"]["force_channel"] = channel
    data["settings"]["force_channel_url"] = "https://t.me/" + channel[1:]
    save_data()
    await message.reply_text(
        f"✅ تم تفعيل الاشتراك الإجباري بالقناة {channel}.\n"
        "تأكد أن البوت مشرف بالقناة حتى يستطيع التحقق من الاشتراك."
    )


async def disable_force_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_bot_owner(user.id):
        await message.reply_text("⛔ هذا الإعداد خاص بمالك البوت الأساسي.")
        return
    data["settings"]["force_channel"] = None
    data["settings"]["force_channel_url"] = None
    save_data()
    await message.reply_text("✅ تم تعطيل الاشتراك الإجباري.")


async def show_force_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_bot_owner(user.id):
        await message.reply_text("⛔ هذا الإعداد خاص بمالك البوت الأساسي.")
        return
    channel = data["settings"].get("force_channel")
    await message.reply_text(
        f"📢 قناة الاشتراك الحالية: {channel}" if channel
        else "ℹ️ الاشتراك الإجباري غير مفعّل."
    )




async def private_settings_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Arabic owner controls in private chat; no Arabic slash commands required."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if not is_bot_owner(user.id):
        if not await force_subscription_ok(user.id, context):
            await send_subscription_gate(message, context)
        else:
            await message.reply_text(
                "استخدم /start لعرض قائمة PARADOX 404."
            )
        return

    text = (message.text or "").strip()
    parts = text.split()
    command = parts[0] if parts else ""

    if command == "تفعيل الاشتراك":
        if len(parts) < 2 or not parts[1].startswith("@"):
            await message.reply_text(
                "اكتب هكذا:\nتفعيل الاشتراك @قناتك\n\n"
                "لازم البوت يكون مشرفاً بالقناة حتى يتحقق من اشتراك الأعضاء."
            )
            return
        channel = parts[1]
        data["settings"]["force_channel"] = channel
        data["settings"]["force_channel_url"] = "https://t.me/" + channel[1:]
        save_data()
        await message.reply_text(
            f"✅ تم تفعيل الاشتراك الإجباري بالقناة {channel}."
        )
        return

    if command == "تعطيل الاشتراك":
        data["settings"]["force_channel"] = None
        data["settings"]["force_channel_url"] = None
        save_data()
        await message.reply_text("✅ تم تعطيل الاشتراك الإجباري.")
        return

    if command == "حالة الاشتراك":
        channel = data["settings"].get("force_channel")
        await message.reply_text(
            f"📢 قناة الاشتراك الحالية: {channel}" if channel
            else "ℹ️ الاشتراك الإجباري غير مفعّل."
        )
        return

    await message.reply_text(
        "أوامر مالك البوت:\n"
        "• تفعيل الاشتراك @قناتك\n"
        "• تعطيل الاشتراك\n"
        "• حالة الاشتراك"
    )


# =========================================================
# Owner/delegate administration
# =========================================================

async def grant_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("رد على رسالة المشرف واكتب /grantunmute.")
        return
    target = message.reply_to_message.from_user
    if not await is_admin(chat.id, target.id, context):
        await message.reply_text("⚠️ الشخص لازم يكون مشرفاً بالمجموعة.")
        return
    info = chat_data(chat.id)
    if str(target.id) not in info["delegates"]:
        info["delegates"].append(str(target.id))
    save_data()
    await message.reply_text(f"✅ مُنح {target.first_name} صلاحية فك الكتم.")


async def revoke_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("رد على رسالة المشرف واكتب /revokeunmute.")
        return
    target = message.reply_to_message.from_user
    info = chat_data(chat.id)
    info["delegates"] = [x for x in info["delegates"] if x != str(target.id)]
    save_data()
    await message.reply_text(f"✅ سُحبت صلاحية فك الكتم من {target.first_name}.")


async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not await can_unmute(chat.id, user.id, context):
        await message.reply_text("⛔ ما عندك صلاحية فك الكتم.")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("رد على رسالة العضو المكتوم واكتب /unmute.")
        return
    target = message.reply_to_message.from_user
    try:
        await unmute_member(chat.id, target.id, context)
        info = chat_data(chat.id)
        info["muted"].pop(str(target.id), None)
        info["warnings"].pop(str(target.id), None)
        save_data()
        await message.reply_text(f"🔊 تم فك الكتم عن {target.first_name}.")
    except TelegramError:
        logger.exception("Unmute failed")
        await message.reply_text("❌ تعذر فك الكتم. افحص صلاحيات البوت.")


async def unmute_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    info = chat_data(chat.id)
    ids = list(info["muted"].keys())
    if not ids:
        await message.reply_text("ℹ️ ماكو أعضاء مسجلين بالكتم عند البوت.")
        return
    success = failed = 0
    for uid in ids:
        try:
            await unmute_member(chat.id, int(uid), context)
            info["muted"].pop(uid, None)
            info["warnings"].pop(uid, None)
            success += 1
        except TelegramError:
            failed += 1
            logger.exception("Could not unmute %s", uid)
    save_data()
    await message.reply_text(
        f"🧹 انتهت العملية.\n✅ فك الكتم: {success}\n❌ تعذر: {failed}"
    )


# =========================================================
# Song library: songs are sent publicly in the group as replies
# =========================================================

async def add_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ إضافة الأغاني للمكتبة خاصة بمالك المجموعة.")
        return
    if not context.args:
        await message.reply_text("رد على ملف صوتي واكتب /addsong اسم الأغنية.")
        return
    if not message.reply_to_message:
        await message.reply_text("لازم ترد على رسالة تحتوي ملفاً صوتياً.")
        return
    replied = message.reply_to_message
    audio = replied.audio
    if audio is None and replied.document and replied.document.mime_type:
        if replied.document.mime_type.startswith("audio/"):
            audio = replied.document
    if audio is None:
        await message.reply_text("❌ الرسالة ما تحتوي ملفاً صوتياً.")
        return
    name = " ".join(context.args).strip()
    info = chat_data(chat.id)
    info["songs"][name.lower()] = {"name": name, "file_id": audio.file_id}
    save_data()
    await message.reply_text(f"✅ تمت إضافة الأغنية: {name}")


async def delete_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    if not context.args:
        await message.reply_text("اكتب /delsong اسم الأغنية.")
        return
    name = " ".join(context.args).strip().lower()
    info = chat_data(chat.id)
    if name not in info["songs"]:
        await message.reply_text("❌ الأغنية غير موجودة بالمكتبة.")
        return
    del info["songs"][name]
    save_data()
    await message.reply_text("🗑️ تم حذف الأغنية من المكتبة.")


async def search_song(message, chat_id: int, query: str):
    info = chat_data(chat_id)
    query = query.strip().lower()
    matches = [
        song for key, song in info["songs"].items()
        if query in key or key in query
    ]
    if not matches:
        await message.reply_text(
            "🔎 ما لقيت الأغنية بمكتبة PARADOX 404 حالياً.\n"
            "تگدر تطلب من مالك المجموعة يضيف ملفها الصوتي المسموح بمشاركته."
        )
        return
    song = matches[0]
    try:
        await message.reply_audio(
            audio=song["file_id"],
            title=song["name"],
            caption=f"🎵 {song['name']}\nبواسطة PARADOX 404",
            reply_to_message_id=message.message_id,
        )
    except TelegramError:
        logger.exception("Song send failed")
        await message.reply_text("❌ تعذر إرسال الملف الصوتي.")


# =========================================================
# Group lists and activity
# =========================================================

async def list_admins(chat_id: int, context) -> str:
    admins = await context.bot.get_chat_administrators(chat_id)
    lines = ["🛡️ مشرفو المجموعة:"]
    for member in admins:
        user = member.user
        role = "👑 المالك" if member.status == "creator" else "🛡️ مشرف"
        lines.append(f"{role}: {user.full_name} — {user.id}")
    return "\n".join(lines)


async def list_tracked(chat_id: int, kind: str) -> str:
    info = chat_data(chat_id)
    labels = {
        "muted": ("🔇 الأعضاء المكتومون", info["muted"]),
        "banned": ("🚫 الأعضاء المحظورون المسجلون", info["banned"]),
        "restricted": ("⚠️ الأعضاء المقيّدون المسجلون", info["restricted"]),
    }
    title, records = labels[kind]
    if not records:
        return title + "\n\nماكو أسماء مسجلة حالياً."
    lines = [title + ":"]
    for uid, record in list(records.items())[:100]:
        name = record.get("name", "عضو") if isinstance(record, dict) else "عضو"
        lines.append(f"• {name} — {uid}")
    return "\n".join(lines)


async def show_top_users(chat_id: int) -> str:
    info = chat_data(chat_id)
    ranked = sorted(
        info["activity"].items(),
        key=lambda item: int(item[1].get("count", 0)),
        reverse=True,
    )[:20]
    if not ranked:
        return "📊 ماكو إحصائيات بعد. يبدأ العد من تشغيل البوت."
    lines = ["📊 أكثر 20 عضواً تفاعلاً:"]
    for index, (uid, record) in enumerate(ranked, start=1):
        name = record.get("name", "عضو")
        count = int(record.get("count", 0))
        lines.append(f"{index}. {name} — {count} رسالة")
    return "\n".join(lines)


async def handle_group_text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    text = (message.text or "").strip()
    key = re.sub(r"\s+", " ", text).strip().lower()

    if key == "المتفاعلين":
        await message.reply_text(await show_top_users(chat.id))
        return True
    if key in ("المشرفون", "المشرفين"):
        try:
            await message.reply_text(await list_admins(chat.id, context))
        except TelegramError:
            await message.reply_text("❌ ما كدرت أجيب قائمة المشرفين.")
        return True
    if key in ("المالكين الأساسيين", "المالك الأساسي", "المالكين"):
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            owners = [m.user for m in admins if m.status == "creator"]
            if owners:
                await message.reply_text(
                    "👑 مالك المجموعة:\n" + "\n".join(f"• {u.full_name} — {u.id}" for u in owners)
                )
            else:
                await message.reply_text("ما ظهر مالك المجموعة.")
        except TelegramError:
            await message.reply_text("❌ تعذر جلب المالك.")
        return True
    if key == "المكتومين":
        await message.reply_text(await list_tracked(chat.id, "muted"))
        return True
    if key == "المحظورين":
        await message.reply_text(await list_tracked(chat.id, "banned"))
        return True
    if key == "المقيدين":
        await message.reply_text(await list_tracked(chat.id, "restricted"))
        return True
    if key == "مسح المكتومين":
        await unmute_all(update, context)
        return True
    return False


# =========================================================
# Moderation, warnings, activity, Nexus keyword
# =========================================================

async def issue_warning(message, chat_id: int, user, context, reason: str):
    info = chat_data(chat_id)
    uid = str(user.id)
    current = min(int(info["warnings"].get(uid, 0)) + 1, WARN_LIMIT)
    info["warnings"][uid] = current
    save_data()

    if current >= WARN_LIMIT:
        if uid in info["muted"]:
            return
        try:
            await mute_member(chat_id, user.id, context)
            info["muted"][uid] = {"name": user.full_name}
            save_data()
            await message.reply_text(
                f"🔇 تم كتم {user.first_name} بعد وصوله إلى "
                f"{WARN_LIMIT} إنذارات.\nالسبب الأخير: {reason}"
            )
        except TelegramError:
            logger.exception("Mute failed")
            await message.reply_text(
                f"⚠️ وصل {user.first_name} إلى {WARN_LIMIT} إنذارات، "
                "لكن تعذر كتمه. افحص صلاحيات البوت."
            )
    else:
        await message.reply_text(
            f"⚠️ تحذير {current} من {WARN_LIMIT} لـ {user.first_name}.\n"
            f"السبب: {reason}"
        )


async def nexus_reply(message):
    await message.reply_text("هلبيك، نيكسوس موجود لخدمتكم 🔥🤖")


async def count_activity(message, chat_id: int, user):
    info = chat_data(chat_id)
    uid = str(user.id)
    record = info["activity"].setdefault(uid, {"name": user.full_name, "count": 0})
    record["name"] = user.full_name
    record["count"] = int(record.get("count", 0)) + 1
    # Save after each message so counts survive restarts.
    save_data()


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or user.is_bot:
        return

    await count_activity(message, chat.id, user)

    if await handle_group_text_commands(update, context):
        return

    text = message.text or message.caption or ""

    song_match = SONG_PATTERN.match(text)
    if song_match:
        await search_song(message, chat.id, song_match.group(1))
        return

    if re.search(r"(?<!\w)(نيكسوس|نكسوس)(?!\w)", text, re.IGNORECASE):
        await nexus_reply(message)
        return

    # Do not moderate group administrators.
    if await is_admin(chat.id, user.id, context):
        return

    if LINK_PATTERN.search(text):
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Could not delete link")
        await issue_warning(message, chat.id, user, context, "نشر رابط")
        return

    normalized = text.lower()
    if any(word in normalized for word in BAD_WORDS):
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Could not delete abusive message")
        await issue_warning(message, chat.id, user, context, "لفظ مسيء")


# =========================================================
# Startup
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير موجود في متغيرات Railway.")
    if not BOT_OWNER_ID or not BOT_OWNER_ID.isdigit():
        raise RuntimeError(
            "أضف BOT_OWNER_ID في Railway Variables، وهو رقم حساب مالك البوت الأساسي."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))

    # Arabic owner settings in private chat.
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            private_settings_text,
        )
    )

    # Existing administration commands.
    app.add_handler(CommandHandler("grantunmute", grant_unmute))
    app.add_handler(CommandHandler("revokeunmute", revoke_unmute))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("unmuteall", unmute_all))
    app.add_handler(CommandHandler("addsong", add_song))
    app.add_handler(CommandHandler("delsong", delete_song))

    # Catch group messages (including Arabic text commands).
    app.add_handler(
        MessageHandler(
            (filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION)),
            moderate,
        )
    )

    logger.info("PARADOX 404 is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
