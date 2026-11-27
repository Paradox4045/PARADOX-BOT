
import os
import re
import json
import time
import asyncio
import logging
import tempfile
import difflib
import unicodedata
from pathlib import Path
from collections import defaultdict, deque
from urllib.parse import quote

import requests

from telegram import (
    Update, ChatPermissions, InlineKeyboardButton,
    InlineKeyboardMarkup, InputFile,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# =========================================================
# PARADOX 404 - NEXUS
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("PARADOX_404")
# منع تسجيل روابط Telegram التي تحتوي على رمز البوت السري.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "").strip()
DATA_FILE = Path(os.getenv("DATA_FILE", "paradox_data.json"))

WARN_LIMIT = 4
SPAM_WINDOW_SECONDS = 6
SPAM_MESSAGE_LIMIT = 5
SPAM_BURST_SECONDS = 3
SPAM_BURST_LIMIT = 4

# الحماية من الرسائل المتشابهة
DUPLICATE_WINDOW_SECONDS = 20
DUPLICATE_SIMILARITY_LIMIT = 0.90
DUPLICATE_MIN_LENGTH = 8
DUPLICATE_HISTORY_LIMIT = 12
DUPLICATE_MATCH_LIMIT = 2

DELETE_MAX_MESSAGES = 1000
DELETE_TRACK_LIMIT = 3000
DELETE_MAX_AGE = 48 * 60 * 60

BAD_WORDS = ["كلب", "حمار", "غبي", "حقير", "تافه"]
LINK_PATTERN = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.I)
SONG_PATTERN = re.compile(r"^\s*يوت\s+(.+)$", re.I)

IA_SEARCH_URL = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata/"
IA_MAX_FILE_SIZE = 45 * 1024 * 1024

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
duplicate_tracker = defaultdict(deque)

group_message_history = defaultdict(
    lambda: deque(maxlen=DELETE_TRACK_LIMIT)
)


# =========================================================
# SMART TEXT PROTECTION
# =========================================================

def normalize_message(text):
    """تنظيف النص حتى نكشف التشابه رغم اختلاف الرموز والتشكيل."""
    text = (text or "").lower()
    text = unicodedata.normalize("NFKC", text)

    cleaned = []
    for char in text:
        category = unicodedata.category(char)
        if category.startswith("M"):
            continue
        if char in "ـ":
            continue
        if category.startswith("P") or category.startswith("S"):
            cleaned.append(" ")
        else:
            cleaned.append(char)

    text = "".join(cleaned)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def contains_message_link(message, text):
    """يكشف الروابط الظاهرة والمخفية داخل النص أو الكابتشن."""
    if LINK_PATTERN.search(text or ""):
        return True
    entities = list(message.entities or []) + list(message.caption_entities or [])
    return any(getattr(entity, "type", "") in ("url", "text_link") for entity in entities)


def contains_bad_word(text):
    """يطابق الألفاظ المسيئة ككلمات مستقلة لتقليل الإنذارات الخاطئة."""
    normalized_words = set(normalize_message(text).split())
    return any(normalize_message(word) in normalized_words for word in BAD_WORDS)


def is_duplicate_message(chat_id, user_id, text):
    """يكشف تكرار أو تشابه رسائل العضو خلال فترة قصيرة."""
    normalized = normalize_message(text)

    if len(normalized) < DUPLICATE_MIN_LENGTH:
        return False

    key = (chat_id, user_id)
    now = time.time()
    history = duplicate_tracker[key]

    while history and now - history[0][0] > DUPLICATE_WINDOW_SECONDS:
        history.popleft()

    similar_matches = 0

    for old_time, old_text in history:
        if len(old_text) < DUPLICATE_MIN_LENGTH:
            continue

        similarity = difflib.SequenceMatcher(
            None, normalized, old_text
        ).ratio()

        if similarity >= DUPLICATE_SIMILARITY_LIMIT:
            similar_matches += 1

    history.append((now, normalized))

    while len(history) > DUPLICATE_HISTORY_LIMIT:
        history.popleft()

    # ننتظر رسالتين سابقتين متشابهتين قبل اعتبار الرسالة الحالية تكراراً.
    return similar_matches >= DUPLICATE_MATCH_LIMIT


# =========================================================
# DATA
# =========================================================

def load_data():
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
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

# استعادة سجل الرسائل من التخزين الدائم حتى يستمر أمر التنظيف بعد إعادة التشغيل.
for _chat_id, _info in data.get("chats", {}).items():
    _history = _info.get("message_history", [])
    if isinstance(_history, list):
        group_message_history[int(_chat_id)] = deque(
            (
                item for item in _history[-DELETE_TRACK_LIMIT:]
                if isinstance(item, dict)
                and "id" in item and "date" in item
            ),
            maxlen=DELETE_TRACK_LIMIT,
        )


def save_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DATA_FILE.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
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
    info["locks"].setdefault("spam", True)
    info.setdefault("admin_permissions", {})
    info.setdefault("logs", [])
    info.setdefault("welcome", True)
    info.setdefault("message_history", [])
    return info


def is_bot_owner(user_id):
    return bool(BOT_OWNER_ID) and str(user_id) == BOT_OWNER_ID


def log_action(chat_id, actor, action, target=None):
    info = chat_data(chat_id)
    info["logs"].append({
        "time": int(time.time()),
        "actor": actor.full_name if actor else "النظام",
        "actor_id": actor.id if actor else 0,
        "action": action,
        "target": target.full_name if target else "",
        "target_id": target.id if target else 0,
    })
    info["logs"] = info["logs"][-100:]
    save_data()


# =========================================================
# MESSAGE TRACKING AND BULK DELETE
# =========================================================

def track_group_message(message):
    if not message or not message.chat:
        return
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    item = {
        "id": message.message_id,
        "date": message.date.timestamp(),
    }
    history = group_message_history[message.chat.id]
    history.append(item)
    info = chat_data(message.chat.id)
    info["message_history"] = list(history)
    save_data()


async def delete_recent_messages(message, chat_id, actor, amount, context):
    if amount < 1 or amount > DELETE_MAX_MESSAGES:
        await message.reply_text(
            f"اكتب رقماً من 1 إلى {DELETE_MAX_MESSAGES}."
        )
        return

    if not await require_permission(
        message, chat_id, actor, "delete", context
    ):
        return

    try:
        bot_member = await context.bot.get_chat_member(
            chat_id, context.bot.id
        )
        if (
            bot_member.status not in ("administrator", "creator")
            or not getattr(bot_member, "can_delete_messages", False)
        ):
            await message.reply_text(
                "❌ لازم ترفع البوت مشرفاً وتفعّل صلاحية حذف الرسائل."
            )
            return
    except TelegramError:
        await message.reply_text(
            "❌ ما كدرت أتأكد من صلاحيات البوت."
        )
        return

    now = time.time()
    history = group_message_history[chat_id]

    candidates = [
        item for item in reversed(history)
        if item["id"] != message.message_id
        and 0 <= now - item["date"] < DELETE_MAX_AGE
    ]

    deleted_ids = set()
    deleted_count = 0

    for item in candidates:
        if deleted_count >= amount:
            break
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=item["id"],
            )
            deleted_ids.add(item["id"])
            deleted_count += 1
        except TelegramError:
            continue

    if deleted_ids:
        group_message_history[chat_id] = deque(
            (
                item for item in history
                if item["id"] not in deleted_ids
            ),
            maxlen=DELETE_TRACK_LIMIT,
        )
        chat_data(chat_id)["message_history"] = list(
            group_message_history[chat_id]
        )
        save_data()

    try:
        await message.delete()
    except TelegramError:
        pass

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🧹 اكتملت عملية التنظيف.\n"
            f"✅ الرسائل المحذوفة: {deleted_count} من {amount}"
        ),
    )

    log_action(
        chat_id, actor,
        f"حذف جماعي: {deleted_count} رسالة",
    )


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
    saved = chat_data(chat_id)["admin_permissions"].get(str(user_id))
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
# FORCED SUBSCRIPTION
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
    rows.append([InlineKeyboardButton(
        "✅ تحقق", callback_data="check_subscription"
    )])
    return InlineKeyboardMarkup(rows)


async def send_subscription_gate(message):
    await message.reply_text(
        "🔒 الاشتراك مطلوب\n\n"
        "اشترك بقناة المالك لاستخدام خدمات البوت بالخاص، ثم اضغط تحقق.",
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
        rows.append([InlineKeyboardButton(
            "📢 اشتراك", callback_data="force_menu"
        )])
    return InlineKeyboardMarkup(rows)


def back_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ رجوع", callback_data="home")
    ]])


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
    saved = chat_data(chat_id)["admin_permissions"].get(str(target_id), {})
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
        if (
            not is_bot_owner(user.id)
            and not await force_subscription_ok(user.id, context)
        ):
            await send_subscription_gate(message)
            return
        await message.reply_text(
            "🔥 أهلاً بك في PARADOX 404\n\nأنا نيكسوس 🤖\nاختار القسم:",
            reply_markup=main_menu(user.id),
        )
    else:
        await message.reply_text(
            "🔥 PARADOX 404 موجود لخدمتكم. "
            "افتح الخاص واضغط /start للإعدادات."
        )


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
            await query.answer(
                "اشترك بالقناة أولاً ثم أعد المحاولة.",
                show_alert=True,
            )
        return

    if action.startswith("perm:"):
        try:
            _, chat_id, target_id, permission = action.split(":")
            chat_id, target_id = int(chat_id), int(target_id)
        except (ValueError, TypeError):
            await query.answer("بيانات غير صحيحة.", show_alert=True)
            return

        if not await is_group_owner(chat_id, user.id, context):
            await query.answer(
                "هذه القائمة لمالك المجموعة فقط.", show_alert=True
            )
            return

        target = await get_member(chat_id, target_id, context)
        if not target or target.status != "administrator":
            await query.answer("هذا المستخدم لم يعد مشرفاً.", show_alert=True)
            return

        saved = chat_data(chat_id)["admin_permissions"].setdefault(
            str(target_id), {}
        )
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

    owner_actions = (
        "force_menu", "force_activate", "force_disable", "force_status"
    )
    if action in owner_actions and not is_bot_owner(user.id):
        await query.answer(
            "هذا القسم خاص بمالك البوت الأساسي.", show_alert=True
        )
        return

    if (
        query.message
        and query.message.chat.type == ChatType.PRIVATE
        and not is_bot_owner(user.id)
        and not await force_subscription_ok(user.id, context)
    ):
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
            "الحماية التلقائية: حذف الروابط والألفاظ، "
            "وكشف الرسائل المتشابهة والإغراق، "
            "وإنذار ثم كتم بعد 4 إنذارات.",
            reply_markup=back_keyboard(),
        )

    elif action == "statistics":
        await query.edit_message_text(
            "📊 الإحصائيات والقوائم\n\n"
            "المتفاعلين\nالمشرفون\nالمالكين\n"
            "المكتومين\nالمحظورين\nالمقيدين\nسجل الإدارة",
            reply_markup=back_keyboard(),
        )

    elif action == "permissions_help":
        await query.edit_message_text(
            "👮 صلاحيات المشرفين\n\n"
            "مالك المجموعة يرد على رسالة المشرف ويكتب:\n"
            "صلاحيات المشرف\n\n"
            "ثم يحدد صلاحياته من الأزرار. كل مشرف له إعداد مستقل.",
            reply_markup=back_keyboard(),
        )

    elif action == "commands_help":
        await query.edit_message_text(
            "📋 أوامر الإدارة (بالرد على رسالة العضو)\n\n"
            "كتم — إلغاء الكتم — حظر — إلغاء الحظر — طرد\n"
            "تحذير — تقييد — إلغاء التقييد — حذف — حذف 100\n"
            "تثبيت — إلغاء التثبيت — معلومات العضو\n"
            "رفع مشرف — تنزيل مشرف — صلاحيات المشرف\n"
            "مسح المكتومين — مسح الإنذارات\n"
            "تفعيل الترحيب — إيقاف الترحيب\n\n"
            "للأغاني: اكتب يوت ثم اسم الأغنية.",
            reply_markup=back_keyboard(),
        )

    elif action == "force_menu":
        channel = data["settings"].get("force_channel")
        status = (
            f"القناة الحالية: {channel}"
            if channel else "الاشتراك الإجباري غير مفعّل."
        )
        await query.edit_message_text(
            "📢 الاشتراك الإجباري\n\n" + status + "\n\nاختار العملية:",
            reply_markup=subscription_menu(),
        )

    elif action == "force_activate":
        await query.edit_message_text(
            "أرسل بالخاص:\nتفعيل الاشتراك @قناتك\n\n"
            "لازم البوت يكون مشرفاً بالقناة.",
            reply_markup=back_keyboard(),
        )

    elif action == "force_status":
        channel = data["settings"].get("force_channel")
        text = (
            f"📢 الاشتراك مفعّل.\nالقناة: {channel}"
            if channel else "ℹ️ الاشتراك غير مفعّل."
        )
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

async def private_settings_text(update, context):
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if not is_bot_owner(user.id):
        if not await force_subscription_ok(user.id, context):
            await send_subscription_gate(message)
        else:
            await message.reply_text(
                "استخدم /start لعرض قائمة PARADOX 404."
            )
        return

    parts = (message.text or "").strip().split()
    if not parts:
        return

    command = " ".join(parts[:2]) if len(parts) >= 2 else parts[0]

    if command == "تصدير البيانات":
        try:
            with DATA_FILE.open("rb") as backup_file:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=InputFile(
                        backup_file, filename="paradox_data_backup.json"
                    ),
                    caption=(
                        "🛡️ نسخة احتياطية من بيانات PARADOX 404. "
                        "احتفظ بهذا الملف حتى نكمل نقل التخزين الدائم."
                    ),
                )
            await message.reply_text("✅ أرسلت النسخة الاحتياطية إلى محادثتك الخاصة.")
        except FileNotFoundError:
            await message.reply_text("ℹ️ ماكو ملف بيانات محفوظ حالياً.")
        except TelegramError:
            logger.exception("Could not export data backup")
            await message.reply_text("❌ تعذر إرسال النسخة الاحتياطية.")
        return

    if command == "تفعيل الاشتراك":
        if len(parts) < 3 or not parts[2].startswith("@"):
            await message.reply_text("اكتب هكذا:\nتفعيل الاشتراك @قناتك")
            return
        channel = parts[2]
        try:
            bot_member = await context.bot.get_chat_member(
                channel, context.bot.id
            )
            if bot_member.status not in ("administrator", "creator"):
                await message.reply_text(
                    "❌ لازم ترفع البوت مشرفاً بالقناة أولاً."
                )
                return
        except TelegramError:
            await message.reply_text(
                "❌ ما كدرت أوصل للقناة. تأكد من المعرف وأن البوت مشرف."
            )
            return

        data["settings"]["force_channel"] = channel
        data["settings"]["force_channel_url"] = (
            "https://t.me/" + channel[1:]
        )
        save_data()
        await message.reply_text(
            f"✅ تم تفعيل الاشتراك الإجباري بالقناة {channel}."
        )

    elif command == "تعطيل الاشتراك":
        data["settings"]["force_channel"] = None
        data["settings"]["force_channel_url"] = None
        save_data()
        await message.reply_text("✅ تم تعطيل الاشتراك الإجباري.")

    elif command == "حالة الاشتراك":
        channel = data["settings"].get("force_channel")
        await message.reply_text(
            f"📢 القناة الحالية: {channel}"
            if channel else "ℹ️ الاشتراك غير مفعّل."
        )

    else:
        await message.reply_text(
            "أوامر الاشتراك:\n"
            "تفعيل الاشتراك @قناتك\n"
            "تعطيل الاشتراك\n"
            "حالة الاشتراك"
        )


async def import_data_backup(update, context):
    global data
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not is_bot_owner(user.id):
        return
    document = message.document
    if not document or not (document.file_name or "").lower().endswith(".json"):
        await message.reply_text("أرسل ملف النسخة الاحتياطية بصيغة JSON.")
        return
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        raw = await telegram_file.download_as_bytearray()
        candidate = json.loads(bytes(raw).decode("utf-8"))
        if (
            not isinstance(candidate, dict)
            or not isinstance(candidate.get("chats"), dict)
            or not isinstance(candidate.get("settings"), dict)
        ):
            raise ValueError("Invalid backup structure")
        candidate["settings"].setdefault("force_channel", None)
        candidate["settings"].setdefault("force_channel_url", None)
        temp_path = DATA_FILE.with_suffix(".import.tmp")
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8") as output:
            json.dump(candidate, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, DATA_FILE)
        data = candidate
        await message.reply_text(
            "✅ تم استيراد النسخة الاحتياطية بنجاح وحفظ بيانات المجموعات."
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        await message.reply_text("❌ ملف النسخة الاحتياطية غير صالح.")
    except Exception:
        logger.exception("Data backup import failed")
        await message.reply_text("❌ تعذر استيراد النسخة الاحتياطية.")


# =========================================================
# MODERATOR PERMISSIONS
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
        await message.reply_text(
            "رد على رسالة المشرف واكتب: صلاحيات المشرف"
        )
        return

    target = message.reply_to_message.from_user
    member = await get_member(chat.id, target.id, context)
    if not member or member.status != "administrator":
        await message.reply_text(
            "⚠️ لازم تختار شخصاً مشرفاً حالياً بالمجموعة."
        )
        return

    chat_data(chat.id)["admin_permissions"].setdefault(str(target.id), {})
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
        "restrict": "mute", "unrestrict": "mute",
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
        chat_data(chat.id)["warnings"].clear()
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
            await message.reply_text(
                "❌ تعذر حذف الرسالة. افحص صلاحية البوت."
            )
        return

    if action in ("pin", "unpin"):
        if not message.reply_to_message:
            await message.reply_text(
                "رد على الرسالة واكتب: تثبيت أو إلغاء التثبيت"
            )
            return
        try:
            if action == "pin":
                await message.reply_to_message.pin()
                await message.reply_text("📌 تم تثبيت الرسالة.")
            else:
                await message.reply_to_message.unpin()
                await message.reply_text("تم إلغاء التثبيت.")
            log_action(
                chat.id, actor,
                "تثبيت" if action == "pin" else "إلغاء تثبيت",
            )
        except TelegramError:
            await message.reply_text(
                "❌ تعذر تنفيذ العملية. افحص صلاحيات البوت."
            )
        return

    if action == "clearwarn" and target:
        chat_data(chat.id)["warnings"].pop(str(target.id), None)
        save_data()
        await message.reply_text(
            f"✅ تم مسح إنذارات {target.first_name}."
        )
        log_action(chat.id, actor, "مسح الإنذارات", target)
        return

    if not target:
        await message.reply_text(
            "رد على رسالة العضو أولاً ثم اكتب الأمر."
        )
        return

    if target.id == actor.id or target.is_bot:
        await message.reply_text(
            "⛔ ما أگدر أنفذ الأمر على نفسك أو على بوت."
        )
        return

    try:
        if action == "mute":
            await mute_member(chat.id, target.id, context)
            chat_data(chat.id)["muted"][str(target.id)] = {
                "name": target.full_name
            }
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

        elif action == "restrict":
            permissions = ChatPermissions(
                can_send_messages=True,
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
                chat.id, target.id, permissions=permissions,
                use_independent_chat_permissions=True,
            )
            chat_data(chat.id)["restricted"][str(target.id)] = {
                "name": target.full_name
            }
            log_action(chat.id, actor, "تقييد", target)
            await message.reply_text(
                f"⚠️ تم تقييد {target.first_name}. يگدر يكتب نص فقط."
            )

        elif action == "unrestrict":
            await unmute_member(chat.id, target.id, context)
            chat_data(chat.id)["restricted"].pop(str(target.id), None)
            save_data()
            log_action(chat.id, actor, "إلغاء التقييد", target)
            await message.reply_text(
                f"✅ تم إلغاء التقييد عن {target.first_name}."
            )

        elif action == "ban":
            await context.bot.ban_chat_member(chat.id, target.id)
            chat_data(chat.id)["banned"][str(target.id)] = {
                "name": target.full_name
            }
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
            await issue_warning(
                message, chat.id, target, context, "إنذار يدوي"
            )

        elif action == "info":
            member = await get_member(chat.id, target.id, context)
            status = member.status if member else "غير معروف"
            warnings = chat_data(chat.id)["warnings"].get(
                str(target.id), 0
            )
            await message.reply_text(
                f"👤 معلومات العضو\n"
                f"الاسم: {target.full_name}\n"
                f"المعرف: {target.id}\n"
                f"الحالة: {status}\n"
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
            await message.reply_text(
                f"🛡️ تمت ترقية {target.first_name} إلى مشرف."
            )
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
            chat_data(chat.id)["admin_permissions"].pop(
                str(target.id), None
            )
            await message.reply_text(
                f"تم تنزيل {target.first_name} من الإشراف."
            )
            log_action(chat.id, actor, "تنزيل مشرف", target)

        save_data()

    except TelegramError:
        logger.exception("Admin action failed: %s", action)
        await message.reply_text(
            "❌ تعذر تنفيذ الأمر. تأكد من صلاحيات البوت ورتبة العضو."
        )


# =========================================================
# LOCKS
# =========================================================

async def set_lock(message, chat_id, actor, lock_name, enabled, context):
    if not await require_permission(
        message, chat_id, actor, "protection", context
    ):
        return
    info = chat_data(chat_id)
    info["locks"][lock_name] = bool(enabled)
    save_data()
    state = "قفل" if enabled else "فتح"
    await message.reply_text(
        f"✅ تم {state} {LOCK_TYPES[lock_name]}."
    )
    log_action(chat_id, actor, f"{state} {LOCK_TYPES[lock_name]}")


# =========================================================
# LISTS AND STATS
# =========================================================

async def list_admins(chat_id, context):
    admins = await context.bot.get_chat_administrators(chat_id)
    lines = ["🛡️ مشرفو المجموعة:"]
    for member in admins:
        user = member.user
        role = "👑 المالك" if member.status == "creator" else "🛡️ مشرف"
        lines.append(f"{role}: {user.full_name} — {user.id}")
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
    ranked = sorted(
        chat_data(chat_id)["activity"].items(),
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
    if not await require_permission(
        message, chat_id, actor, "stats", context
    ):
        return
    logs = chat_data(chat_id)["logs"][-15:]
    if not logs:
        await message.reply_text("📋 ماكو عمليات مسجلة بعد.")
        return
    lines = ["📋 آخر عمليات الإدارة:"]
    for item in reversed(logs):
        when = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(item["time"])
        )
        target = f" ← {item['target']}" if item.get("target") else ""
        lines.append(
            f"• {when} | {item['actor']}: {item['action']}{target}"
        )
    await message.reply_text("\n".join(lines))


# =========================================================
# SONG LIBRARY
# =========================================================

async def add_song(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text(
            "⛔ إضافة الأغاني للمكتبة خاصة بمالك المجموعة."
        )
        return
    if not context.args or not message.reply_to_message:
        await message.reply_text(
            "رد على ملف صوتي واكتب /addsong اسم الأغنية."
        )
        return
    replied = message.reply_to_message
    audio = replied.audio
    if (
        audio is None and replied.document
        and replied.document.mime_type
        and replied.document.mime_type.startswith("audio/")
    ):
        audio = replied.document
    if audio is None:
        await message.reply_text("❌ الرسالة ما تحتوي ملفاً صوتياً.")
        return
    name = " ".join(context.args).strip()
    chat_data(chat.id)["songs"][name.lower()] = {
        "name": name, "file_id": audio.file_id
    }
    save_data()
    await message.reply_text(f"✅ تمت إضافة الأغنية: {name}")


async def delete_song(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
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


# =========================================================
# INTERNET ARCHIVE AUDIO SEARCH
# =========================================================

def ia_license_allowed(license_url):
    if not license_url:
        return False
    license_url = str(license_url).lower().strip()
    allowed = (
        "creativecommons.org/publicdomain/mark/",
        "creativecommons.org/publicdomain/zero/",
        "creativecommons.org/licenses/by/",
        "creativecommons.org/licenses/by-sa/",
        "creativecommons.org/licenses/by-nd/",
    )
    return any(item in license_url for item in allowed)


def ia_find_audio(query):
    temp_path = None
    try:
        safe_query = query.replace('"', " ").replace("\\", " ")
        search_params = [
            ("q", f'mediatype:audio AND (title:"{safe_query}" OR creator:"{safe_query}")'),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "creator"),
            ("fl[]", "licenseurl"),
            ("rows", "8"),
            ("page", "1"),
            ("output", "json"),
        ]
        response = requests.get(
            IA_SEARCH_URL, params=search_params, timeout=(10, 20)
        )
        response.raise_for_status()
        documents = response.json().get("response", {}).get("docs", [])

        for item in documents:
            identifier = item.get("identifier")
            if not identifier:
                continue

            metadata_response = requests.get(
                IA_METADATA_URL + quote(identifier, safe=""),
                timeout=(10, 20),
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            details = metadata.get("metadata", {})

            if details.get("mediatype") != "audio":
                continue

            license_url = (
                details.get("licenseurl") or item.get("licenseurl") or ""
            )
            if not ia_license_allowed(license_url):
                continue

            mp3_files = []
            for file_info in metadata.get("files", []):
                filename = file_info.get("name", "")
                size = file_info.get("size", "")
                if not filename.lower().endswith(".mp3"):
                    continue
                if not str(size).isdigit():
                    continue
                size = int(size)
                if size <= 0 or size > IA_MAX_FILE_SIZE:
                    continue
                if str(file_info.get("private", "")).lower() == "true":
                    continue
                mp3_files.append(file_info)

            if not mp3_files:
                continue

            mp3_files.sort(key=lambda f: int(f.get("size", 0)))
            filename = mp3_files[0]["name"]
            download_url = (
                "https://archive.org/download/"
                + quote(identifier, safe="")
                + "/" + quote(filename, safe="/")
            )
            audio_response = requests.get(
                download_url, stream=True, timeout=(15, 45)
            )
            audio_response.raise_for_status()

            temp = tempfile.NamedTemporaryFile(
                suffix=".mp3", delete=False
            )
            temp_path = temp.name
            total_size = 0
            try:
                with temp:
                    for chunk in audio_response.iter_content(
                        chunk_size=256 * 1024
                    ):
                        if not chunk:
                            continue
                        total_size += len(chunk)
                        if total_size > IA_MAX_FILE_SIZE:
                            raise ValueError("Audio file exceeds size limit")
                        temp.write(chunk)
            finally:
                audio_response.close()

            return {
                "path": temp_path,
                "title": details.get("title", item.get("title", query)),
                "creator": details.get("creator", "غير معروف"),
                "license": license_url,
                "page": "https://archive.org/details/" + quote(identifier, safe=""),
            }

    except Exception:
        logger.exception("Internet Archive search failed")
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    return None


async def search_song(message, chat_id, query):
    query = query.strip()
    if not query:
        await message.reply_text("اكتب اسم الأغنية بعد كلمة يوت.")
        return

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

    status_message = await message.reply_text(
        f"🔎 نيكسوس يبحث عن: {query}\n"
        "🎵 جاري البحث عن ملف صوتي قابل للإرسال..."
    )
    result = None
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(ia_find_audio, query),
            timeout=65,
        )
        if not result:
            await status_message.edit_text(
                "❌ ما حصلت ملفاً صوتياً مناسباً ومتاحاً.\n\n"
                "جرّب اسماً آخر، أو أضف الأغنية إلى مكتبة المجموعة.\n"
                "ملاحظة: البحث يقتصر على التسجيلات التي تعلن "
                "تراخيص تسمح بإعادة الاستخدام."
            )
            return

        with open(result["path"], "rb") as audio_file:
            await message.reply_audio(
                audio=InputFile(audio_file, filename="PARADOX_404.mp3"),
                title=str(result["title"])[:64],
                performer=str(result["creator"])[:64],
                caption=(
                    f"🎵 {result['title']}\n"
                    f"👤 الفنان/الرافع: {result['creator']}\n"
                    f"📜 الترخيص المعلن: {result['license']}\n"
                    f"🌐 المصدر: {result['page']}\n\n"
                    "🔥 PARADOX 404"
                ),
                reply_to_message_id=message.message_id,
            )
        await status_message.delete()

    except asyncio.TimeoutError:
        logger.warning("Internet Archive search timed out")
        await status_message.edit_text(
            "⏳ البحث أخذ وقتاً طويلاً.\nجرّب مرة ثانية بعد قليل."
        )
    except Exception:
        logger.exception("Audio search or sending failed")
        try:
            await status_message.edit_text(
                "⚠️ تعذر تحميل أو إرسال الملف الصوتي حالياً.\n"
                "حاول مرة ثانية بعد قليل."
            )
        except TelegramError:
            pass
    finally:
        if result and result.get("path") and os.path.exists(result["path"]):
            try:
                os.remove(result["path"])
            except OSError:
                logger.exception("Temporary audio cleanup failed")


# =========================================================
# GROUP TEXT COMMANDS
# =========================================================

async def handle_group_text_commands(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if (
        not message or not chat or not user
        or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
    ):
        return False

    text = (message.text or "").strip()
    key = re.sub(r"\s+", " ", text).strip().lower()

    delete_match = re.fullmatch(r"حذف\s+(\d+)", key)
    if delete_match:
        await delete_recent_messages(
            message,
            chat.id,
            user,
            int(delete_match.group(1)),
            context,
        )
        return True

    if key == "المتفاعلين":
        if await has_permission(chat.id, user.id, "stats", context):
            await message.reply_text(await show_top_users(chat.id))
        else:
            await message.reply_text("⛔ ما عندك صلاحية عرض الإحصائيات.")
        return True

    if key in ("المشرفون", "المشرفين"):
        if not await require_permission(
            message, chat.id, user, "stats", context
        ):
            return True
        try:
            await message.reply_text(await list_admins(chat.id, context))
        except TelegramError:
            await message.reply_text("❌ ما كدرت أجيب قائمة المشرفين.")
        return True

    if key in ("المالكين", "المالك"):
        if not await require_permission(
            message, chat.id, user, "stats", context
        ):
            return True
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            owners = [
                member.user for member in admins
                if member.status == "creator"
            ]
            await message.reply_text(
                "👑 مالك المجموعة:\n" + "\n".join(
                    f"• {u.full_name} — {u.id}" for u in owners
                )
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
            if await require_permission(
                message, chat.id, user, "stats", context
            ):
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

    if key in ("تفعيل الترحيب", "فتح الترحيب", "إيقاف الترحيب", "قفل الترحيب"):
        if not await require_permission(
            message, chat.id, user, "protection", context
        ):
            return True
        enabled = key in ("تفعيل الترحيب", "فتح الترحيب")
        chat_data(chat.id)["welcome"] = enabled
        save_data()
        await message.reply_text(
            "✅ تم تفعيل الترحيب." if enabled else "✅ تم إيقاف الترحيب."
        )
        log_action(chat.id, user, "تفعيل الترحيب" if enabled else "إيقاف الترحيب")
        return True

    for lock_name, label in LOCK_TYPES.items():
        if (
            key in (f"قفل {label}", f"قفل {lock_name}")
            or (lock_name == "spam" and key == "قفل السبام")
        ):
            await set_lock(
                message, chat.id, user, lock_name, True, context
            )
            return True
        if (
            key in (f"فتح {label}", f"فتح {lock_name}")
            or (lock_name == "spam" and key == "فتح السبام")
        ):
            await set_lock(
                message, chat.id, user, lock_name, False, context
            )
            return True

    actions = {
        "كتم": "mute",
        "إلغاء الكتم": "unmute",
        "فك الكتم": "unmute",
        "تقييد": "restrict",
        "إلغاء التقييد": "unrestrict",
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
    """يسجل الإنذار ويرسل التنبيه إلى المجموعة حتى لو حُذفت الرسالة الأصلية."""
    info = chat_data(chat_id)
    uid = str(user.id)
    current = min(
        int(info["warnings"].get(uid, 0)) + 1,
        WARN_LIMIT,
    )
    info["warnings"][uid] = current
    save_data()

    if current >= WARN_LIMIT:
        if uid in info["muted"]:
            return
        try:
            await mute_member(chat_id, user.id, context)
            info["muted"][uid] = {"name": user.full_name}
            save_data()
            notice = (
                f"🔇 تم كتم {user.first_name} بعد {WARN_LIMIT} إنذارات.\n"
                f"السبب الأخير: {reason}"
            )
            log_action(chat_id, None, "كتم تلقائي بعد الإنذارات", user)
        except TelegramError:
            logger.exception("Automatic mute failed")
            notice = (
                f"⚠️ وصل {user.first_name} إلى {WARN_LIMIT} إنذارات، "
                "لكن تعذر كتمه. تأكد من صلاحيات البوت."
            )
    else:
        notice = (
            f"⚠️ تحذير {current} من {WARN_LIMIT} لـ {user.first_name}.\n"
            f"السبب: {reason}"
        )

    try:
        await context.bot.send_message(chat_id=chat_id, text=notice)
    except TelegramError:
        logger.exception("Could not send moderation warning to chat")


async def count_activity(chat_id, user):
    info = chat_data(chat_id)
    uid = str(user.id)
    record = info["activity"].setdefault(
        uid, {"name": user.full_name, "count": 0}
    )
    record["name"] = user.full_name
    record["count"] = int(record.get("count", 0)) + 1
    save_data()


async def nexus_reply(message):
    await message.reply_text("هلبيك، نيكسوس موجود لخدمتكم 🔥🤖")


async def welcome_new_members(update, context):
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


async def unmute_all(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return

    info = chat_data(chat.id)
    ids = list(info["muted"].keys())
    if not ids:
        await message.reply_text(
            "ℹ️ ماكو أعضاء مسجلين بالكتم عند البوت."
        )
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

async def moderate(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if (
        not message or not chat or not user
        or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
    ):
        return

    if user.is_bot:
        return

    track_group_message(message)
    await count_activity(chat.id, user)

    if await handle_group_text_commands(update, context):
        return

    text = message.text or message.caption or ""

    if re.search(r"(?<!\w)(نيكسوس|نكسوس)(?!\w)", text, re.I):
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

    if locks.get("links", True) and contains_message_link(message, text):
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Could not delete locked link")
        await issue_warning(
            message, chat.id, user, context, "نشر رابط"
        )
        return

    if locks.get("mentions"):
        mentions = len(re.findall(r"@\w+", text))
        if mentions >= 5:
            try:
                await message.delete()
            except TelegramError:
                logger.exception("Mention spam delete failed")
            await issue_warning(
                message, chat.id, user, context, "منشنات جماعية"
            )
            return

    if contains_bad_word(text):
        try:
            await message.delete()
        except TelegramError:
            logger.exception("Could not delete abusive message")
        await issue_warning(
            message, chat.id, user, context, "لفظ مسيء"
        )
        return

    # الحماية الذكية: كشف الرسائل المتطابقة والمتشابهة
    if locks.get("spam", True) and text:
        if is_duplicate_message(chat.id, user.id, text):
            try:
                await message.delete()
            except TelegramError:
                logger.exception("Duplicate message delete failed")
            await issue_warning(
                message,
                chat.id,
                user,
                context,
                "تكرار أو تشابه الرسائل",
            )
            return

    # كشف الإغراق: 5 رسائل خلال 6 ثوانٍ، منها 4 خلال آخر 3 ثوانٍ.
    if locks.get("spam", True):
        key = (chat.id, user.id)
        now = time.time()
        queue = spam_tracker[key]
        queue.append(now)

        while queue and now - queue[0] > SPAM_WINDOW_SECONDS:
            queue.popleft()

        burst_count = sum(
            1 for sent_at in queue
            if now - sent_at <= SPAM_BURST_SECONDS
        )

        if (
            len(queue) >= SPAM_MESSAGE_LIMIT
            and burst_count >= SPAM_BURST_LIMIT
        ):
            queue.clear()
            try:
                await message.delete()
            except TelegramError:
                logger.exception("Flood message delete failed")
            await issue_warning(
                message, chat.id, user, context, "إغراق سريع بالرسائل"
            )


# =========================================================
# DELEGATED UNMUTE
# =========================================================

async def grant_unmute(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    target = await reply_target(message)
    if not target:
        await message.reply_text(
            "رد على رسالة المشرف واكتب /grantunmute."
        )
        return
    if not await is_admin(chat.id, target.id, context):
        await message.reply_text(
            "⚠️ الشخص لازم يكون مشرفاً بالمجموعة."
        )
        return
    info = chat_data(chat.id)
    if str(target.id) not in info["delegates"]:
        info["delegates"].append(str(target.id))
    save_data()
    await message.reply_text(
        f"✅ مُنح {target.first_name} صلاحية فك الكتم."
    )
    log_action(chat.id, user, "منح صلاحية فك الكتم", target)


async def revoke_unmute(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if not await is_group_owner(chat.id, user.id, context):
        await message.reply_text("⛔ هذا الأمر خاص بمالك المجموعة.")
        return
    target = await reply_target(message)
    if not target:
        await message.reply_text(
            "رد على رسالة المشرف واكتب /revokeunmute."
        )
        return
    info = chat_data(chat.id)
    info["delegates"] = [
        item for item in info["delegates"] if item != str(target.id)
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
        raise RuntimeError(
            "BOT_TOKEN غير موجود في متغيرات Railway."
        )
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

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Document.ALL,
        import_data_backup,
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
