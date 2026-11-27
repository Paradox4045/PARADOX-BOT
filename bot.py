
import os
import re
import json
import time
import asyncio
import logging
from pathlib import Path
from collections import defaultdict, deque

import yt_dlp

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
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# =========================================================
# PARADOX 404 - NEXUS
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
SPAM_WINDOW_SECONDS = 10
SPAM_MESSAGE_LIMIT = 6

BAD_WORDS = ["كلب", "حمار", "غبي", "حقير", "تافه"]
LINK_PATTERN = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
SONG_PATTERN = re.compile(r"^\s*يوت\s+(.+)$", re.IGNORECASE)

PERMISSIONS = {
    "protection": "الحماية",
    "mute": "الكتم وفك الكتم",
    "ban": "الحظر والطرد",
    "warnings": "الإنذارات",
    "delete": "حذف الرسائل والتنظيف",
    "pin": "تثبيت الرسائل",
    "stats": "الإحصائيات والقوائم",
    "admins": "إدارة المشرفين",
}

LOCK_TYPES = {
    "links": "الروابط",
    "photos": "الصور",
    "videos": "الفيديو",
    "files": "الملفات",
    "forwards": "التوجيه",
    "stickers": "الملصقات",
    "voice": "الرسائل الصوتية",
    "polls": "الاستطلاعات",
    "spam": "الإغراق والتكرار",
    "mentions": "المنشنات الجماعية",
}

DEFAULT_DATA = {
    "chats": {},
    "settings": {"force_channel": None, "force_channel_url": None},
}

spam_tracker = defaultdict(deque)


# =========================================================
# DATA
# =========================================================

def load_data():
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if not isinstance(loaded, dict):
            raise ValueError("Invalid data")
        loaded.setdefault("chats", {})
        loaded.setdefault("settings", {})
        loaded["settings"].setdefault("force_channel", None)
        loaded["settings"].setdefault("force_channel_url", None)
        return loaded
    except FileNotFoundError:
        return json.loads(json.dumps(DEFAULT_DATA))
    except (json.JSONDecodeError, ValueError):
        logger.exception("Data file damaged")
        return json.loads(json.dumps(DEFAULT_DATA))


data = load_data()


def save_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DATA_FILE.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, DATA_FILE)


def chat_data(chat_id):
    info = data["chats"].setdefault(str(chat_id), {})
    info.setdefault("warnings", {})
    info.setdefault("muted", {})
    info.setdefault("banned", {})
    info.setdefault("restricted", {})
    info.setdefault("delegates", [])
    info.setdefault("songs", {})
    info.setdefault("activity", {})
    info.setdefault("locks", {})
    info["locks"].setdefault("links", True)
    info.setdefault("admin_permissions", {})
    info.setdefault("logs", [])
    info.setdefault("welcome", True)
    return info


def is_bot_owner(user_id):
    return bool(BOT_OWNER_ID) and str(user_id) == BOT_OWNER_ID


def log_action(chat_id, actor, action, target=None):
    info = chat_data(chat_id)
    entry = {
        "time": int(time.time()),
        "actor": actor.full_name if actor else "النظام",
        "actor_id": actor.id if actor else 0,
        "action": action,
        "target": target.full_name if target else "",
        "target_id": target.id if target else 0,
    }
    info["logs"].append(entry)
    info["logs"] = info["logs"][-100:]
    save_data()


# =========================================================
# ADMIN AND CUSTOM PERMISSIONS
# =========================================================

async def get_member(chat_id, user_id, context):
    try:
        return await context.bot.get_chat_member(chat_id, user_id)
    except TelegramError:
        return None


async def is_group_owner(chat_id, user_id, context):
    member = await get_member(chat_id, user_id, context)
    return bool(member and member.status == "creator")


async def is_admin(chat_id, user_id, context):
    member = await get_member(chat_id, user_id, context)
    return bool(member and member.status in ("administrator", "creator"))


async def has_permission(chat_id, user_id, permission, context):
    if await is_group_owner(chat_id, user_id, context):
        return True
    member = await get_member(chat_id, user_id, context)
    if not member or member.status != "administrator":
        return False
    info = chat_data(chat_id)
    saved = info["admin_permissions"].get(str(user_id))
    return bool(saved and saved.get(permission, False))


async def require_permission(message, chat_id, user, permission, context):
    if await has_permission(chat_id, user.id, permission, context):
        return True
    await message.reply_text("⛔ ما عندك صلاحية استخدام هذا الأمر.")
    return False


# =========================================================
# TELEGRAM PERMISSIONS
# =========================================================

def normal_permissions():
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
        use_independent_chat_permissions=True,
    )


async def unmute_member(chat_id, user_id, context):
    chat = await context.bot.get_chat(chat_id)
    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=chat.permissions or normal_permissions(),
        use_independent_chat_permissions=True,
    )


# =========================================================
# FORCED SUBSCRIPTION - PRIVATE CHAT ONLY
# =========================================================

async def force_subscription_ok(user_id, context):
    channel = data["settings"].get("force_channel")
    if not channel:
        return True
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ("creator", "administrator", "member")
    except TelegramError:
        return False


def force_keyboard():
    url = data["settings"].get("force_channel_url")
    channel = data["settings"].get("force_channel")
    if not url and channel:
        url = "https://t.me/" + str(channel).lstrip("@")
    rows = []
    if url:
        rows.append([InlineKeyboardButton("📢 اشترك", url=url)])
    rows.append([InlineKeyboardButton("✅ تحقق", callback_data="check_subscription")])
    return InlineKeyboardMarkup(rows)


async def send_subscription_gate(message):
    await message.reply_text(
        "🔒 الاشتراك مطلوب\n\nاشترك بقناة المالك لاستخدام خدمات البوت بالخاص، ثم اضغط تحقق.",
        reply_markup=force_keyboard(),
    )


# =========================================================
# MENUS
# =========================================================

def main_menu(user_id=None):
    rows = [
        [
            InlineKeyboardButton("🛡️ حماية", callback_data="management"),
            InlineKeyboardButton("📊 إحصاء", callback_data="statistics"),
        ],
        [
            InlineKeyboardButton("👮 صلاحيات", callback_data="permissions_help"),
            InlineKeyboardButton("📋 الأوامر", callback_data="commands_help"),
        ],
    ]
    if user_id is not None and is_bot_owner(user_id):
        rows.append([InlineKeyboardButton("📢 اشتراك", callback_data="force_menu")])
    return InlineKeyboardMarkup(rows)


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="home")]])


def subscription_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ إضافة قناة", callback_data="force_activate"),
            InlineKeyboardButton("📋 الحالة", callback_data="force_status"),
        ],
        [InlineKeyboardButton("❌ تعطيل", callback_data="force_disable")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="home")],
    ])


def permissions_keyboard(chat_id, target_id):
    info = chat_data(chat_id)
    saved = info["admin_permissions"].get(str(target_id), {})
    rows = []
    for key, label in PERMISSIONS.items():
        mark = "✅" if saved.get(key, False) else "❌"
        rows.append([InlineKeyboardButton(
            f"{mark} {label}",
            callback_data=f"perm:{chat_id}:{target_id}:{key}",
        )])
    rows.append([InlineKeyboardButton("إغلاق", callback_data="perm_close")])
    return InlineKeyboardMarkup(rows)


# =========================================================
# START AND CALLBACKS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if message.chat.type == ChatType.PRIVATE:
        if not is_bot_owner(user.id) and not await force_subscription_ok(user.id, context):
            await send_subscription_gate(message)
            return
        await message.reply_text(
            "🔥 أهلاً بك في PARADOX 404\n\nأنا نيكسوس 🤖\nاختار القسم:",
            reply_markup=main_menu(user.id),
        )
    else:
        await message.reply_text("🔥 PARADOX 404 موجود لخدمتكم. افتح الخاص واضغط /start للإعدادات.")


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    action = query.data or ""

    if action == "check_subscription":
        if is_bot_owner(user.id) or await force_subscription_ok(user.id, context):
            await query.answer("تم التحقق بنجاح ✅")
            await query.edit_message_text(
                "✅ تم التحقق. أهلاً بك في PARADOX 404!",
                reply_markup=main_menu(user.id),
            )
        else:
            await query.answer("اشترك بالقناة أولاً ثم أعد المحاولة.", show_alert=True)
        return

    if action.startswith("perm:"):
        try:
            _, chat_id, target_id, permission = action.split(":")
            chat_id, target_id = int(chat_id), int(target_id)
        except (ValueError, TypeError):
            await query.answer("بيانات غير صحيحة.", show_alert=True)
            return
        if not await is_group_owner(chat_id, user.id, context):
            await query.answer("هذه القائمة لمالك المجموعة فقط.", show_alert=True)
            return
        target = await get_member(chat_id, target_id, context)
        if not target or target.status != "administrator":
            await query.answer("هذا المستخدم لم يعد مشرفاً.", show_alert=True)
            return
        info = chat_data(chat_id)
        saved = info["admin_permissions"].setdefault(str(target_id), {})
        saved[permission] = not saved.get(permission, False)
        save_data()
        await query.answer("تم تحديث الصلاحية.")
        await query.edit_message_reply_markup(
            reply_markup=permissions_keyboard(chat_id, target_id)
        )
        return

    if action == "perm_close":
        await query.answer()
        await query.edit_message_text("تم إغلاق إعدادات الصلاحيات.")
        return

    owner_actions = ("force_menu", "force_activate", "force_disable", "force_status")
    if action in owner_actions and not is_bot_owner(user.id):
        await query.answer("هذا القسم خاص بمالك البوت الأساسي.", show_alert=True)
        return

    if query.message and query.message.chat.type == ChatType.PRIVATE and not is_bot_owner(user.id):
        if not await force_subscription_ok(user.id, context):
            await query.answer("اشترك بالقناة أولاً.", show_alert=True)
            return

    await query.answer()

    if action == "home":
        await query.edit_message_text(
            "🔥 PARADOX 404\n\nاختار القسم:",
            reply_markup=main_menu(user.id),
        )
    elif action == "management":
        await query.edit_message_text(
            "🛡️ الحماية والإدارة\n\n"
            "أوامر القفل والفتح داخل المجموعة:\n"
            "قفل الروابط / فتح الروابط\n"
            "قفل الصور / فتح الصور\n"
            "قفل الفيديو / فتح الفيديو\n"
            "قفل الملفات / فتح الملفات\n"
            "قفل التوجيه / فتح التوجيه\n"
            "قفل الملصقات / فتح الملصقات\n"
            "قفل الصوت / فتح الصوت\n"
            "قفل الاستطلاعات / فتح الاستطلاعات\n"
            "قفل السبام / فتح السبام\n"
            "قفل المنشنات / فتح المنشنات\n\n"
            "الحماية التلقائية: حذف الروابط والألفاظ، وإنذار ثم كتم بعد 4 إنذارات.",
            reply_markup=back_keyboard(),
        )
    elif action == "statistics":
        await query.edit_message_text(
            "📊 الإحصائيات والقوائم\n\n"
            "المتفاعلين\nالمشرفون\nالمالكين\nالمكتومين\nالمحظورين\nالمقيدين\nسجل الإدارة",
            reply_markup=back_keyboard(),
        )
    elif action == "permissions_help":
        await query.edit_message_text(
            "👮 صلاحيات المشرفين\n\n"
            "مالك المجموعة يرد على رسالة المشرف ويكتب:\nصلاحيات المشرف\n\n"
            "ثم يحدد صلاحياته من الأزرار. كل مشرف له إعداد مستقل.",
            reply_markup=back_keyboard(),
        )
    elif action == "commands_help":
        await query.edit_message_text(
            "📋 أوامر الإدارة (بالرد على رسالة العضو)\n\n"
            "كتم — إلغاء الكتم — حظر — إلغاء الحظر — طرد\n"
            "تحذير — حذف — تثبيت — إلغاء التثبيت\n"
            "معلومات العضو — رفع مشرف — تنزيل مشرف — صلاحيات المشرف\n"
            "مسح المكتومين — مسح الإنذارات\n\n"
            "مكتبة الأغاني وبياناتها محفوظة، وزرها مخفي من القائمة حالياً.",
            reply_markup=back_keyboard(),
        )
    elif action == "force_menu":
        channel = data["settings"].get("force_channel")
        status = f"القناة الحالية: {channel}" if channel else "الاشتراك الإجباري غير مفعّل."
        await query.edit_message_text(
            "📢 الاشتراك الإجباري\n\n" + status + "\n\nاختار العملية:",
            reply_markup=subscription_menu(),
        )
    elif action == "force_activate":
        await query.edit_message_text(
            "أرسل بالخاص:\nتفعيل الاشتراك @قناتك\n\nلازم البوت يكون مشرفاً بالقناة.",
            reply_markup=back_keyboard(),
        )
    elif action == "force_status":
        channel = data["settings"].get("force_channel")
        text = f"📢 الاشتراك مفعّل.\nالقناة: {channel}" if channel else "ℹ️ الاشتراك غير مفعّل."
        await query.edit_message_text(text, reply_markup=subscription_menu())
    elif action == "force_disable":
        data["settings"]["force_channel"] = None
        data["settings"]["force_channel_url"] = None
        save_data()
        await query.edit_message_text(
            "✅ تم تعطيل الاشتراك الإجباري.",
            reply_markup=subscription_menu(),
        )


# =========================================================
# OWNER PRIVATE SETTINGS
# =========================================================

async def private_settings_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not is_bot_owner(user.id):
        if not await force_subscription_ok(user.id, context):
            await send_subscription_gate(message)
        else:
            await message.reply_text("استخدم /start لعرض قائمة PARADOX 404.")
        return

    parts = (message.text or "").strip().split()
    if not parts:
        return
    command = " ".join(parts[:2]) if len(parts) >= 2 else parts[0]

    if command == "تفعيل الاشتراك":
        if len(parts) < 3 or not parts[2].startswith("@"):
            await message.reply_text("اكتب هكذا:\nتفعيل الاشتراك @قناتك")
            return
        channel = parts[2]
        try:
            bot_member = await context.bot.get_chat_member(channel, context.bot.id)
            if bot_member.status not in ("administrator", "creator"):
                await message.reply_text("❌ لازم ترفع البوت مشرفاً بالقناة أولاً.")
                return
        except TelegramError:
            await message.reply_text("❌ ما كدرت أوصل للقناة. تأكد من المعرف وأن البوت مشرف.")
            return
        data["settings"]["force_channel"] = channel
        data["settings"]["force_channel_url"] = "https://t.me/" + channel[1:]
        save_data()
        await message.reply_text(f"✅ تم تفعيل الاشتراك الإجباري بالقناة {channel}.")
    elif command == "تعطيل الاشتراك":
        data["settings"]["force_channel"] = None
        data["settings"]["force_channel_url"] = None
        save_data()
        await message.reply_text("✅ تم تعطيل الاشتراك الإجباري.")
    elif command == "حالة الاشتراك":
        channel = data["settings"].get("force_channel")
        await message.reply_text(
            f"📢 القناة الحالية: {channel}" if channel else "ℹ️ الاشتراك غير مفعّل."
        )
    else:
        await message.reply_text(
            "أوامر الاشتراك:\nتفعيل الاشتراك @قناتك\nتعطيل الاشتراك\nحالة الاشتراك"
        )


# =========================================================
# PER-MODERATOR PERMISSION MENU
# =========================================================

async def show_admin_permissions(update, context):
    message = update.effective_message
    chat = update.effective_chat
    actor = update.effective_user
    if not message or not chat or not actor:
        return
    if not await is_group_owner(chat.id, actor.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("رد على رسالة المشرف واكتب: صلاحيات المشرف")
        return
    target = message.reply_to_message.from_user
    member = await get_member(chat.id, target.id, context)
    if not member or member.status != "administrator":
        await message.reply_text("⚠️ لازم تختار شخصاً مشرفاً حالياً بالمجموعة.")
        return
    info = chat_data(chat.id)
    info["admin_permissions"].setdefault(str(target.id), {})
    save_data()
    await message.reply_text(
        f"👮 صلاحيات {target.full_name}\nاختر الصلاحيات المسموحة:",
        reply_markup=permissions_keyboard(chat.id, target.id),
    )


# =========================================================
# ADMIN ACTIONS
# =========================================================

async def reply_target(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return None


async def admin_action(update, context, action):
    message = update.effective_message
    chat = update.effective_chat
    actor = update.effective_user
    if not message or not chat or not actor:
        return
    target = await reply_target(message)

    permission_for_action = {
        "mute": "mute", "unmute": "mute",
        "ban": "ban", "unban": "ban", "kick": "ban",
        "warn": "warnings", "clearwarn": "warnings",
        "delete": "delete", "pin": "pin", "unpin": "pin",
        "info": "stats", "promote": "admins", "demote": "admins",
    }[action]

    if action == "unmute":
        info = chat_data(chat.id)
        delegated = (
            str(actor.id) in info["delegates"]
            and await is_admin(chat.id, actor.id, context)
        )
        if not delegated and not await require_permission(
            message, chat.id, actor, "mute", context
        ):
            return
    elif not await require_permission(
        message, chat.id, actor, permission_for_action, context
    ):
        return

    if action == "clearwarn" and not target:
        info = chat_data(chat.id)
        info["warnings"].clear()
        save_data()
        await message.reply_text("🧹 تم مسح إنذارات جميع الأعضاء.")
        log_action(chat.id, actor, "مسح إنذارات الجميع")
        return

    if action == "delete":
        if not message.reply_to_message:
            await message.reply_text("رد على الرسالة واكتب: حذف")
            return
        try:
            await message.reply_to_message.delete()
            await message.reply_text("🗑️ تم حذف الرسالة.")
            log_action(chat.id, actor, "حذف رسالة")
        except TelegramError:
            await message.reply_text("❌ تعذر حذف الرسالة. افحص صلاحية البوت.")
        return

    if action in ("pin", "unpin"):
        if not message.reply_to_message:
            await message.reply_text("رد على الرسالة واكتب: تثبيت أو إلغاء التثبيت")
            return
        try:
            if action == "pin":
                await message.reply_to_message.pin()
                await message.reply_text("📌 تم تثبيت الرسالة.")
            else:
                await message.reply_to_message.unpin()
                await message.reply_text("تم إلغاء التثبيت.")
            log_action(chat.id, actor, "تثبيت" if action == "pin" else "إلغاء تثبيت")
        except TelegramError:
            await message.reply_text("❌ تعذر تنفيذ العملية. افحص صلاحيات البوت.")
        return

    if action == "clearwarn" and target:
        chat_data(chat.id)["warnings"].pop(str(target.id), None)
        save_data()
        await message.reply_text(f"✅ تم مسح إنذارات {target.first_name}.")
        log_action(chat.id, actor, "مسح الإنذارات", target)
        return

    if not target:
        await message.reply_text("رد على رسالة العضو أولاً ثم اكتب الأمر.")
        return

    if target.id == actor.id or target.is_bot:
        await message.reply_text("⛔ ما أگدر أنفذ الأمر على نفسك أو على بوت.")
        return

    try:
        if action == "mute":
            await mute_member(chat.id, target.id, context)
            chat_data(chat.id)["muted"][str(target.id)] = {"name": target.full_name}
            log_action(chat.id, actor, "كتم", target)
            await message.reply_text(f"🔇 تم كتم {target.first_name}.")
        elif action == "unmute":
            await unmute_member(chat.id, target.id, context)
            info = chat_data(chat.id)
            info["muted"].pop(str(target.id), None)
            info["warnings"].pop(str(target.id), None)
            save_data()
            log_action(chat.id, actor, "فك الكتم", target)
            await message.reply_text(f"🔊 تم فك الكتم عن {target.first_name}.")
        elif action == "ban":
            await context.bot.ban_chat_member(chat.id, target.id)
            chat_data(chat.id)["banned"][str(target.id)] = {"name": target.full_name}
            log_action(chat.id, actor, "حظر", target)
            await message.reply_text(f"🚫 تم حظر {target.first_name}.")
        elif action == "unban":
            await context.bot.unban_chat_member(
                chat.id, target.id, only_if_banned=True
            )
            chat_data(chat.id)["banned"].pop(str(target.id), None)
            save_data()
            log_action(chat.id, actor, "إلغاء الحظر", target)
            await message.reply_text(f"✅ تم إلغاء حظر {target.first_name}.")
        elif action == "kick":
            await context.bot.ban_chat_member(chat.id, target.id)
            await context.bot.unban_chat_member(
                chat.id, target.id, only_if_banned=True
            )
            log_action(chat.id, actor, "طرد", target)
            await message.reply_text(f"👋 تم طرد {target.first_name}.")
        elif action == "warn":
            await issue_warning(message, chat.id, target, context, "إنذار يدوي")
        elif action == "info":
            member = await get_member(chat.id, target.id, context)
            status = member.status if member else "غير معروف"
            warnings = chat_data(chat.id)["warnings"].get(str(target.id), 0)
            await message.reply_text(
                f"👤 معلومات العضو\nالاسم: {target.full_name}\n"
                f"المعرف: {target.id}\nالحالة: {status}\n"
                f"الإنذارات: {warnings}/{WARN_LIMIT}"
            )
        elif action == "promote":
            await context.bot.promote_chat_member(
                chat.id, target.id,
                can_manage_chat=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=True,
                can_manage_video_chats=False,
                can_manage_topics=False,
            )
            await message.reply_text(f"🛡️ تمت ترقية {target.first_name} إلى مشرف.")
            log_action(chat.id, actor, "رفع مشرف", target)
        elif action == "demote":
            await context.bot.promote_chat_member(
                chat.id, target.id,
                can_manage_chat=False,
                can_delete_messages=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_manage_video_chats=False,
                can_manage_topics=False,
            )
            chat_data(chat.id)["admin_permissions"].pop(str(target.id), None)
            await message.reply_text(f"تم تنزيل {target.first_name} من الإشراف.")
            log_action(chat.id, actor, "تنزيل مشرف", target)
        save_data()
    except TelegramError:
        logger.exception("Admin action failed: %s", action)
        await message.reply_text(
            "❌ تعذر تنفيذ الأمر. تأكد من صلاحيات البوت ورتبة العضو."
        )


# =========================================================
# LOCKS / UNLOCKS
# =========================================================

async def set_lock(message, chat_id, actor, lock_name, enabled, context):
    if not await require_permission(message, chat_id, actor, "protection", context):
        return
    info = chat_data(chat_id)
    info["locks"][lock_name] = bool(enabled)
    save_data()
    state = "قفل" if enabled else "فتح"
    await message.reply_text(f"✅ تم {state} {LOCK_TYPES[lock_name]}.")
    log_action(chat_id, actor, f"{state} {LOCK_TYPES[lock_name]}")


# =========================================================
# LISTS AND STATS
# =========================================================

async def list_admins(chat_id, context):
    admins = await context.bot.get_chat_administrators(chat_id)
    lines = ["🛡️ مشرفو المجموعة:"]
    for member in admins:
        u = member.user
        role = "👑 المالك" if member.status == "creator" else "🛡️ مشرف"
        lines.append(f"{role}: {u.full_name} — {u.id}")
    return "\n".join(lines)


async def list_tracked(chat_id, kind):
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


async def show_top_users(chat_id):
    info = chat_data(chat_id)
    ranked = sorted(
        info["activity"].items(),
        key=lambda item: int(item[1].get("count", 0)),
        reverse=True,
    )[:20]
    if not ranked:
        return "📊 ماكو إحصائيات بعد."
    lines = ["📊 أكثر 20 عضواً تفاعلاً:"]
    for index, (uid, record) in enumerate(ranked, start=1):
        lines.append(
            f"{index}. {record.get('name', 'عضو')} — "
            f"{int(record.get('count', 0))} رسالة"
        )
    return "\n".join(lines)


async def show_logs(message, chat_id, actor, context):
    if not await require_permission(message, chat_id, actor, "stats", context):
        return
    logs = chat_data(chat_id)["logs"][-15:]
    if not logs:
        await message.reply_text("📋 ماكو عمليات مسجلة بعد.")
        return
    lines = ["📋 آخر عمليات الإدارة:"]
    for item in reversed(logs):
        when = time.strftime(
            "%Y-%m-%d %H:%M",
            time.localtime(item["time"]),
        )
        target = f" ← {item['target']}" if item.get("target") else ""
        lines.append(
            f"• {when} | {item['actor']}: {item['action']}{target}"
        )
    await message.reply_text("\n".join(lines))


# =========================================================
# SONG LIBRARY AND YOUTUBE SEARCH
# =========================================================

async def add_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ إضافة الأغاني للمكتبة خاصة بمالك المجموعة.")
        return
    if not context.args or not message.reply_to_message:
        await message.reply_text("رد على ملف صوتي واكتب /addsong اسم الأغنية.")
        return
    replied = message.reply_to_message
    audio = replied.audio
    if (
        audio is None
        and replied.document
        and replied.document.mime_type
        and replied.document.mime_type.startswith("audio/")
    ):
        audio = replied.document
    if audio is None:
        await message.reply_text("❌ الرسالة ما تحتوي ملفاً صوتياً.")
        return
    name = " ".join(context.args).strip()
    chat_data(chat.id)["songs"][name.lower()] = {
        "name": name,
        "file_id": audio.file_id,
    }
    save_data()
    await message.reply_text(f"✅ تمت إضافة الأغنية: {name}")


async def delete_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    name = " ".join(context.args).strip().lower()
    if not name:
        await message.reply_text("اكتب /delsong اسم الأغنية.")
        return
    songs = chat_data(chat.id)["songs"]
    if name not in songs:
        await message.reply_text("❌ الأغنية غير موجودة بالمكتبة.")
        return
    del songs[name]
    save_data()
    await message.reply_text("🗑️ تم حذف الأغنية من المكتبة.")


async def search_song(message, chat_id, query):
    query = query.strip()

    if not query:
        await message.reply_text("اكتب اسم الأغنية بعد كلمة يوت.")
        return

    # البحث أولاً في مكتبة PARADOX 404 القديمة
    songs = chat_data(chat_id)["songs"]
    normalized = query.lower()

    matches = [
        song for key, song in songs.items()
        if normalized in key or key in normalized
    ]

    if matches:
        song = matches[0]
        try:
            await message.reply_audio(
                audio=song["file_id"],
                title=song["name"],
                caption=f"🎵 {song['name']}\nبواسطة PARADOX 404",
                reply_to_message_id=message.message_id,
            )
            return
        except TelegramError:
            logger.exception("Song library send failed")

    # إذا لم توجد بالمكتبة، نبحث في YouTube
    await message.reply_text(f"🔎 دا أبحث عن: {query}")

    def youtube_search():
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "ignoreerrors": True,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(
                f"ytsearch5:{query}",
                download=False,
            )

    try:
        result = await asyncio.to_thread(youtube_search)
        entries = (result or {}).get("entries") or []

        if not entries:
            await message.reply_text(
                "❌ ما حصلت نتائج. جرّب اسم أغنية ثاني."
            )
            return

        lines = ["🎵 نتائج البحث في YouTube:\n"]

        for index, item in enumerate(entries, start=1):
            if not item:
                continue

            title = item.get("title") or "بدون عنوان"
            url = item.get("webpage_url") or item.get("url")
            video_id = item.get("id")

            if url and str(url).startswith("http"):
                link = url
            elif video_id:
                link = f"https://www.youtube.com/watch?v={video_id}"
            else:
                continue

            lines.append(f"{index}. {title}\n{link}\n")

        if len(lines) == 1:
            await message.reply_text("❌ ما حصلت نتائج صالحة.")
            return

        await message.reply_text(
            "\n".join(lines),
            disable_web_page_preview=True,
        )

    except Exception:
        logger.exception("YouTube search failed")
        await message.reply_text(
            "⚠️ تعذر البحث حالياً في YouTube. جرّب مرة ثانية بعد قليل."
        )


# =========================================================
# GROUP TEXT COMMANDS
# =========================================================

async def handle_group_text_commands(update, context):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if (
        not message
        or not chat
        or not user
        or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
    ):
        return False

    text = (message.text or "").strip()
    key = re.sub(r"\s+", " ", text).strip().lower()

    if key == "المتفاعلين":
        if await has_permission(chat.id, user.id, "stats", context):
            await message.reply_text(await show_top_users(chat.id))
        else:
            await message.reply_text("⛔ ما عندك صلاحية عرض الإحصائيات.")
        return True

    if key in ("المشرفون", "المشرفين"):
        if not await require_permission(message, chat.id, user, "stats", context):
            return True
        try:
            await message.reply_text(await list_admins(chat.id, context))
        except TelegramError:
            await message.reply_text("❌ ما كدرت أجيب قائمة المشرفين.")
        return True

    if key in ("المالكين", "المالك"):
        if not await require_permission(message, chat.id, user, "stats", context):
            return True
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            owners = [m.user for m in admins if m.status == "creator"]
            await message.reply_text(
                "👑 مالك المجموعة:\n"
                + "\n".join(f"• {u.full_name} — {u.id}" for u in owners)
            )
        except TelegramError:
            await message.reply_text("❌ تعذر جلب المالك.")
        return True

    for key_name, kind in (
        ("المكتومين", "muted"),
        ("المحظورين", "banned"),
        ("المقيدين", "restricted"),
    ):
        if key == key_name:
            if await require_permission(message, chat.id, user, "stats", context):
                await message.reply_text(await list_tracked(chat.id, kind))
            return True

    if key == "سجل الإدارة":
        await show_logs(message, chat.id, user, context)
        return True

    if key == "مسح المكتومين":
        await unmute_all(update, context)
        return True

    if key == "صلاحيات المشرف":
        await show_admin_permissions(update, context)
        return True

    for lock_name, label in LOCK_TYPES.items():
        if key == f"قفل {label}" or key == f"قفل {lock_name}":
            await set_lock(message, chat.id, user, lock_name, True, context)
            return True
        if key == f"فتح {label}" or key == f"فتح {lock_name}":
            await set_lock(message, chat.id, user, lock_name, False, context)
            return True

    actions = {
        "كتم": "mute",
        "إلغاء الكتم": "unmute",
        "فك الكتم": "unmute",
        "حظر": "ban",
        "إلغاء الحظر": "unban",
        "طرد": "kick",
        "تحذير": "warn",
        "مسح الإنذارات": "clearwarn",
        "حذف": "delete",
        "تثبيت": "pin",
        "إلغاء التثبيت": "unpin",
        "معلومات العضو": "info",
        "رفع مشرف": "promote",
        "تنزيل مشرف": "demote",
    }

    if key in actions:
        await admin_action(update, context, actions[key])
        return True

    return False


# =========================================================
# WARNINGS / AUTOMATIC MODERATION
# =========================================================

async def issue_warning(message, chat_id, user, context, reason):
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
                f"🔇 تم كتم {user.first_name} بعد {WARN_LIMIT} إنذارات.\n"
                f"السبب الأخير: {reason}"
            )
            log_action(chat_id, None, "كتم تلقائي بعد الإنذارات", user)
        except TelegramError:
            logger.exception("Automatic mute failed")
            await message.reply_text(
                f"⚠️ وصل {user.first_name} إلى {WARN_LIMIT} إنذارات، "
                "لكن تعذر كتمه."
            )
    else:
        await message.reply_text(
            f"⚠️ تحذير {current} من {WARN_LIMIT} لـ {user.first_name}.\n"
            f"السبب: {reason}"
        )


async def count_activity(chat_id, user):
    info = chat_data(chat_id)
    uid = str(user.id)
    record = info["activity"].setdefault(
        uid,
        {"name": user.full_name, "count": 0},
    )
    record["name"] = user.full_name
    record["count"] = int(record.get("count", 0)) + 1
    save_data()


async def nexus_reply(message):
    await message.reply_text("هلبيك، نيكسوس موجود لخدمتكم 🔥🤖")


async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not message.new_chat_members:
        return
    if not chat_data(chat.id).get("welcome", True):
        return
    for member in message.new_chat_members:
        if not member.is_bot:
            await message.reply_text(
                f"أهلاً وسهلاً {member.first_name} 🌟\n"
                "نورت PARADOX 404!"
            )


async def unmute_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
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
    log_action(chat.id, user, "فك كتم الجميع")
    await message.reply_text(
        f"🧹 انتهت العملية.\n✅ فك الكتم: {success}\n❌ تعذر: {failed}"
    )


# =========================================================
# MODERATION HANDLER
# =========================================================

async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if (
        not message
        or not chat
        or not user
        or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
    ):
        return
    if user.is_bot:
        return

    await count_activity(chat.id, user)

    if await handle_group_text_commands(update, context):
        return

    text = message.text or message.caption or ""

    if re.search(r"(?<!\w)(نيكسوس|نكسوس)(?!\w)", text, re.IGNORECASE):
        await nexus_reply(message)
        return

    song_match = SONG_PATTERN.match(text)
    if song_match:
        await search_song(message, chat.id, song_match.group(1))
        return

    if await is_admin(chat.id, user.id, context):
        return

    locks = chat_data(chat.id)["locks"]

    blocked = None
    if locks.get("photos") and message.photo:
        blocked = "إرسال الصور مقفول"
    elif locks.get("videos") and (message.video or message.animation):
        blocked = "إرسال الفيديو مقفول"
    elif locks.get("files") and message.document:
        blocked = "إرسال الملفات مقفول"
    elif locks.get("stickers") and message.sticker:
        blocked = "إرسال الملصقات مقفول"
    elif locks.get("voice") and (
        message.voice or message.audio or message.video_note
    ):
        blocked = "إرسال الصوتيات مقفول"
    elif locks.get("polls") and message.poll:
        blocked = "الاستطلاعات مقفولة"
    elif locks.get("forwards") and getattr(message, "forward_origin", None):
        blocked = "إعادة التوجيه مقفولة"

    if blocked:
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Locked media delete failed")
        await message.reply_text(f"🔒 {blocked}.")
        return

    if locks.get("links", True) and LINK_PATTERN.search(text):
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Could not delete locked link")
        await issue_warning(message, chat.id, user, context, "نشر رابط")
        return

    if locks.get("mentions"):
        mentions = len(re.findall(r"@\w+", text))
        if mentions >= 5:
            try:
                await message.delete()
            except TelegramError:
                logger.exception("Mention spam delete failed")
            await issue_warning(message, chat.id, user, context, "منشنات جماعية")
            return

    if any(word in text.lower() for word in BAD_WORDS):
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Could not delete abusive message")
        await issue_warning(message, chat.id, user, context, "لفظ مسيء")
        return

    if locks.get("spam", True):
        key = (chat.id, user.id)
        now = time.time()
        queue = spam_tracker[key]
        queue.append(now)
        while queue and now - queue[0] > SPAM_WINDOW_SECONDS:
            queue.popleft()
        if len(queue) >= SPAM_MESSAGE_LIMIT:
            queue.clear()
            try:
                await message.delete()
            except TelegramError:
                pass
            await issue_warning(message, chat.id, user, context, "إغراق الرسائل")


# =========================================================
# DELEGATED UNMUTE
# =========================================================

async def grant_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    target = await reply_target(message)
    if not target:
        await message.reply_text("رد على رسالة المشرف واكتب /grantunmute.")
        return
    if not await is_admin(chat.id, target.id, context):
        await message.reply_text("⚠️ الشخص لازم يكون مشرفاً بالمجموعة.")
        return
    info = chat_data(chat.id)
    if str(target.id) not in info["delegates"]:
        info["delegates"].append(str(target.id))
    save_data()
    await message.reply_text(f"✅ مُنح {target.first_name} صلاحية فك الكتم.")
    log_action(chat.id, user, "منح صلاحية فك الكتم", target)


async def revoke_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = (
        update.effective_message,
        update.effective_chat,
        update.effective_user,
    )
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    target = await reply_target(message)
    if not target:
        await message.reply_text("رد على رسالة المشرف واكتب /revokeunmute.")
        return
    info = chat_data(chat.id)
    info["delegates"] = [
        x for x in info["delegates"] if x != str(target.id)
    ]
    save_data()
    await message.reply_text(
        f"✅ سُحبت صلاحية فك الكتم من {target.first_name}."
    )
    log_action(chat.id, user, "سحب صلاحية فك الكتم", target)


# =========================================================
# STARTUP
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير موجود في متغيرات Railway.")
    if not BOT_OWNER_ID or not BOT_OWNER_ID.isdigit():
        raise RuntimeError(
            "أضف BOT_OWNER_ID في Railway Variables، "
            "وهو رقم حساب مالك البوت الأساسي."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        private_settings_text,
    ))

    app.add_handler(CommandHandler("addsong", add_song))
    app.add_handler(CommandHandler("delsong", delete_song))

    app.add_handler(CommandHandler("grantunmute", grant_unmute))
    app.add_handler(CommandHandler("revokeunmute", revoke_unmute))

    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
        welcome_new_members,
    ))

    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (
            filters.TEXT
            | filters.CAPTION
            | filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.Document.ALL
            | filters.Sticker.ALL
            | filters.VOICE
            | filters.AUDIO
            | filters.VIDEO_NOTE
            | filters.POLL
        ),
        moderate,
    ))

    logger.info("PARADOX 404 is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
