#!/usr/bin/env python3
"""
🎨 Image Magic Bot
Features:
  - Remove background  (remove.bg API)
  - Anime style        (Hugging Face)
  - Pencil sketch      (Pillow - local)
  - Pixel art          (Pillow - local)
  - Add watermark text (Pillow - local)
  - Merge two photos   (Pillow - local)
  - Search images      (Pexels + Unsplash)
  - Random daily image (Pexels)
  - Bilingual EN / AR
  - Railway health-check server
"""

import asyncio
import io
import logging
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters,
)

# ─────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIG  (Railway Environment Variables)
# ─────────────────────────────────────────────────────────────
def _env(k: str) -> str:
    v = os.getenv(k, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {k}")
    return v

TELEGRAM_TOKEN      = _env("TELEGRAM_TOKEN")
REMOVE_BG_API_KEY   = _env("REMOVE_BG_API_KEY")
PEXELS_API_KEY      = _env("PEXELS_API_KEY")
UNSPLASH_ACCESS_KEY = _env("UNSPLASH_ACCESS_KEY")
HF_API_KEY          = _env("HF_API_KEY")

# ─────────────────────────────────────────────────────────────
#  CONVERSATION STATES
# ─────────────────────────────────────────────────────────────
(
    ST_WAIT_IMG,
    ST_WAIT_SECOND_IMG,
    ST_WAIT_WM_TEXT,
    ST_WAIT_SEARCH,
) = range(4)

# ─────────────────────────────────────────────────────────────
#  IN-MEMORY SESSION
# ─────────────────────────────────────────────────────────────
sessions: dict = {}   # uid → {"action", "img_bytes", ...}
langs:    dict = {}   # uid → "en" | "ar"

def lang(uid: int) -> str:
    return langs.get(uid, "en")

def sess(uid: int) -> dict:
    return sessions.setdefault(uid, {})

# ─────────────────────────────────────────────────────────────
#  STRINGS
# ─────────────────────────────────────────────────────────────
TR = {
    "welcome": {
        "en": (
            "🎨 *Welcome to Image Magic Bot\\!*\n\n"
            "I can transform your photos in amazing ways\\.\n\n"
            "📌 *How to use:*\n"
            "• Tap any button below\n"
            "• Then send me a photo\n"
            "• Get your result instantly\\!"
        ),
        "ar": (
            "🎨 *أهلاً بك في بوت السحر البصري\\!*\n\n"
            "أقدر أحوّل صورك بطرق رائعة\\.\n\n"
            "📌 *طريقة الاستخدام:*\n"
            "• اضغط أي زرار\n"
            "• ابعتلي صورة\n"
            "• احصل على النتيجة فوراً\\!"
        ),
    },
    "choose": {
        "en": "✅ Photo received\\! What would you like to do?",
        "ar": "✅ وصلت الصورة\\! إيه اللي تحب أعمله؟",
    },
    "processing": {
        "en": "⏳ Processing your photo, please wait\\.\\.\\.",
        "ar": "⏳ جاري معالجة الصورة، انتظر لحظة\\.\\.\\.",
    },
    "done":     {"en": "✨ Done\\! Here's your result\\.",        "ar": "✨ خلصت\\! هي النتيجة\\."},
    "error":    {"en": "❌ Something went wrong\\. Try again\\.", "ar": "❌ في مشكلة\\. حاول تاني\\."},
    "hf_wait":  {"en": "🤖 AI model loading \\(~30s\\)\\. Please wait\\.\\.\\.",
                 "ar": "🤖 النموذج بيتحمّل \\(~30 ثانية\\)\\. انتظر\\.\\.\\.",},
    "send_photo":   {"en": "📸 Please send me a photo\\.", "ar": "📸 ابعتلي صورة\\." },
    "send_second":  {"en": "📸 Now send the *second photo* to merge with\\.",
                     "ar": "📸 دلوقتي ابعتلي *الصورة التانية* للدمج\\."},
    "send_wm_photo":{"en": "📸 Send me the photo you want to add text to\\.",
                     "ar": "📸 ابعتلي الصورة اللي تحب تضيف عليها نص\\."},
    "send_wm_text": {"en": "✏️ Now send the *text* to write on the photo\\.",
                     "ar": "✏️ دلوقتي ابعتلي *النص* اللي تحب يتكتب\\."},
    "send_search":  {"en": "🔍 Send a keyword to search \\(e\\.g\\. *sunset*, *cats*\\)",
                     "ar": "🔍 ابعتلي كلمة للبحث \\(مثلاً: *غروب*, *قطط*\\)"},
    "no_results":   {"en": "😕 No images found\\. Try another keyword\\!",
                     "ar": "😕 مش لاقي صور\\. جرب كلمة تانية\\!"},
    "daily":        {"en": "🌅 *Daily Random Image\\!*\nEnjoy this beautiful photo 🎉",
                     "ar": "🌅 *صورة اليوم العشوائية\\!*\nاتفضل صورة جميلة ليك 🎉"},
    "cancelled":    {"en": "🚫 Cancelled\\.",        "ar": "🚫 تم الإلغاء\\."},
    "lang_changed": {"en": "✅ Language set to *English* 🇬🇧", "ar": "✅ تم ضبط اللغة على *العربية* 🇸🇦"},
    "help": {
        "en": (
            "📖 *All Commands*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "`/start` — Main menu\n"
            "`/removebg` — Remove background\n"
            "`/anime` — Anime style\n"
            "`/sketch` — Pencil sketch\n"
            "`/pixelart` — Pixel art\n"
            "`/watermark` — Add text to photo\n"
            "`/merge` — Merge two photos\n"
            "`/search [keyword]` — Search photos\n"
            "`/random` — Random photo\n"
            "`/lang` — Switch language EN/AR\n"
            "`/cancel` — Cancel current action\n"
            "`/help` — Show this menu"
        ),
        "ar": (
            "📖 *كل الأوامر*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "`/start` — القائمة الرئيسية\n"
            "`/removebg` — إزالة الخلفية\n"
            "`/anime` — تحويل لأنمي\n"
            "`/sketch` — رسم قلم رصاص\n"
            "`/pixelart` — بيكسل آرت\n"
            "`/watermark` — إضافة نص على صورة\n"
            "`/merge` — دمج صورتين\n"
            "`/search [كلمة]` — بحث عن صور\n"
            "`/random` — صورة عشوائية\n"
            "`/lang` — تغيير اللغة EN/AR\n"
            "`/cancel` — إلغاء العملية\n"
            "`/help` — عرض هذه القائمة"
        ),
    },
}

def t(key: str, uid: int) -> str:
    return TR[key][lang(uid)]

# ─────────────────────────────────────────────────────────────
#  KEYBOARDS
# ─────────────────────────────────────────────────────────────
def main_menu(uid: int) -> InlineKeyboardMarkup:
    is_ar = lang(uid) == "ar"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖼 Remove BG",  callback_data="act_removebg"),
            InlineKeyboardButton("🎌 Anime",       callback_data="act_anime"),
        ],
        [
            InlineKeyboardButton("✏️ Sketch",     callback_data="act_sketch"),
            InlineKeyboardButton("👾 Pixel Art",   callback_data="act_pixelart"),
        ],
        [
            InlineKeyboardButton("💧 Watermark",  callback_data="act_watermark"),
            InlineKeyboardButton("🔀 Merge",       callback_data="act_merge"),
        ],
        [
            InlineKeyboardButton("🔍 Search",     callback_data="act_search"),
            InlineKeyboardButton("🎲 Random",      callback_data="act_random"),
        ],
        [
            InlineKeyboardButton(
                "🇸🇦 العربية" if not is_ar else "🇬🇧 English",
                callback_data="toggle_lang"
            ),
            InlineKeyboardButton("📖 Help", callback_data="show_help"),
        ],
    ])

def back_btn(uid: int) -> InlineKeyboardMarkup:
    label = "🔄 New action" if lang(uid) == "en" else "🔄 عملية جديدة"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data="back_menu")
    ]])

# ─────────────────────────────────────────────────────────────
#  IMAGE PROCESSING  (local — no API needed)
# ─────────────────────────────────────────────────────────────
def pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG", optimize=True)
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()

def make_sketch(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("L")
    inv = Image.fromarray(bytes([255 - b for b in img.tobytes()]), "L")
    blr = inv.filter(ImageFilter.GaussianBlur(radius=18))
    result_bytes_arr = bytearray()
    for a, b in zip(img.tobytes(), blr.tobytes()):
        divisor = 255 - b
        if divisor == 0:
            val = 255
        else:
            val = min(int(a) * 255 // divisor, 255)
        result_bytes_arr.append(val)
    sketch = Image.frombytes("L", img.size, bytes(result_bytes_arr)).convert("RGB")
    return pil_to_bytes(sketch)

def make_pixel_art(raw: bytes, block: int = 12) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    small  = img.resize((max(1, w // block), max(1, h // block)), Image.NEAREST)
    result = small.resize((w, h), Image.NEAREST)
    return pil_to_bytes(result)

def add_watermark(raw: bytes, text: str) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    w, h    = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    fs = max(20, w // 18)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fs
        )
    except Exception:
        font = ImageFont.load_default()
    bb = draw.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x, y   = w - tw - 16, h - th - 16
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 150))
    draw.text((x, y),         text, font=font, fill=(255, 255, 255, 210))
    return pil_to_bytes(Image.alpha_composite(img, overlay).convert("RGB"))

def merge_images(raw1: bytes, raw2: bytes) -> bytes:
    img1 = Image.open(io.BytesIO(raw1)).convert("RGB")
    img2 = Image.open(io.BytesIO(raw2)).convert("RGB")
    ratio = img1.height / img2.height
    img2  = img2.resize((int(img2.width * ratio), img1.height), Image.LANCZOS)
    canvas = Image.new("RGB", (img1.width + img2.width + 6, img1.height), (40, 40, 40))
    canvas.paste(img1, (0, 0))
    canvas.paste(img2, (img1.width + 6, 0))
    return pil_to_bytes(canvas)

def make_anime_local(raw: bytes) -> bytes:
    """Local anime-style fallback using Pillow filters."""
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = ImageEnhance.Color(img).enhance(2.0)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img = img.filter(ImageFilter.EDGE_ENHANCE)
    return pil_to_bytes(img)

# ─────────────────────────────────────────────────────────────
#  REMOVE.BG
# ─────────────────────────────────────────────────────────────
async def api_remove_bg(raw: bytes) -> bytes | None:
    try:
        form = aiohttp.FormData()
        form.add_field("image_file", raw, filename="img.png", content_type="image/png")
        form.add_field("size", "auto")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.remove.bg/v1.0/removebg",
                data=form,
                headers={"X-Api-Key": REMOVE_BG_API_KEY},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 200:
                    return await r.read()
                log.warning(f"remove.bg {r.status}: {await r.text()}")
    except Exception as e:
        log.error(f"remove.bg error: {e}")
    return None

# ─────────────────────────────────────────────────────────────
#  HUGGING FACE  (anime style transfer)
# ─────────────────────────────────────────────────────────────
HF_MODELS = [
    "https://api-inference.huggingface.co/models/Yntec/helloFlatArt",
    "https://api-inference.huggingface.co/models/nitrosocke/Ghibli-Diffusion",
]

async def api_hf_anime(raw: bytes) -> bytes | None:
    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type":  "image/jpeg",
    }
    for model_url in HF_MODELS:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    model_url, data=raw, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as r:
                    body = await r.read()
                    if r.status == 200 and len(body) > 500:
                        return body
                    text = body.decode(errors="ignore")
                    if "loading" in text.lower() or r.status == 503:
                        log.info(f"HF model loading, retrying in 25s...")
                        await asyncio.sleep(25)
                        async with s.post(
                            model_url, data=raw, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=90),
                        ) as r2:
                            body2 = await r2.read()
                            if r2.status == 200 and len(body2) > 500:
                                return body2
        except Exception as e:
            log.warning(f"HF {model_url} error: {e}")
    return None

# ─────────────────────────────────────────────────────────────
#  PEXELS + UNSPLASH
# ─────────────────────────────────────────────────────────────
async def pexels_search(query: str, n: int = 5) -> list[str]:
    url = f"https://api.pexels.com/v1/search?query={query}&per_page={n}&orientation=landscape"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, headers={"Authorization": PEXELS_API_KEY},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    return [p["src"]["large"] for p in (await r.json()).get("photos", [])]
    except Exception as e:
        log.warning(f"Pexels: {e}")
    return []

async def unsplash_random(query: str) -> str | None:
    url = f"https://api.unsplash.com/photos/random?query={query}&orientation=landscape"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    return (await r.json())["urls"]["regular"]
    except Exception as e:
        log.warning(f"Unsplash: {e}")
    return None

async def random_image() -> str | None:
    topics = ["nature","architecture","space","ocean","mountains",
              "city","animals","flowers","travel","technology"]
    topic = random.choice(topics)
    urls  = await pexels_search(topic, n=15)
    return random.choice(urls) if urls else await unsplash_random(topic)

# ─────────────────────────────────────────────────────────────
#  DOWNLOAD TELEGRAM PHOTO
# ─────────────────────────────────────────────────────────────
async def dl_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bytes | None:
    try:
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        buf  = io.BytesIO()
        await file.download_to_memory(buf)
        return buf.getvalue()
    except Exception as e:
        log.error(f"dl_photo: {e}")
    return None

# ─────────────────────────────────────────────────────────────
#  SEND RESULT
# ─────────────────────────────────────────────────────────────
async def send_result(ctx: ContextTypes.DEFAULT_TYPE, uid: int, raw: bytes, caption: str = ""):
    try:
        await ctx.bot.send_photo(
            chat_id=uid, photo=io.BytesIO(raw),
            caption=caption or TR["done"][lang(uid)],
            parse_mode="MarkdownV2",
            reply_markup=back_btn(uid),
        )
    except Exception as e:
        log.error(f"send_result: {e}")
        await ctx.bot.send_message(uid, t("error", uid), parse_mode="MarkdownV2")

# ─────────────────────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        t("welcome", uid), parse_mode="MarkdownV2", reply_markup=main_menu(uid)
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(t("help", uid), parse_mode="MarkdownV2")

async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    langs[uid] = "ar" if lang(uid) == "en" else "en"
    await update.message.reply_text(
        t("lang_changed", uid), parse_mode="MarkdownV2", reply_markup=main_menu(uid)
    )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    sessions.pop(uid, None)
    await update.message.reply_text(
        t("cancelled", uid), parse_mode="MarkdownV2", reply_markup=main_menu(uid)
    )
    return ConversationHandler.END

async def cmd_random(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = await update.message.reply_text(t("processing", uid), parse_mode="MarkdownV2")
    url = await random_image()
    await msg.delete()
    if url:
        await context.bot.send_photo(
            chat_id=uid, photo=url,
            caption=t("daily", uid), parse_mode="MarkdownV2",
            reply_markup=back_btn(uid),
        )
    else:
        await update.message.reply_text(t("error", uid), parse_mode="MarkdownV2")

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if context.args:
        return await _do_search(update, context, " ".join(context.args))
    sess(uid)["action"] = "search"
    await update.message.reply_text(t("send_search", uid), parse_mode="MarkdownV2")
    return ST_WAIT_SEARCH

# ─────────────────────────────────────────────────────────────
#  ACTION ENTRY POINTS  (set action + ask for photo)
# ─────────────────────────────────────────────────────────────
def _action_cmd(action: str):
    async def _h(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        sess(uid)["action"] = action
        key = "send_wm_photo" if action == "watermark" else "send_photo"
        await update.message.reply_text(t(key, uid), parse_mode="MarkdownV2")
        return ST_WAIT_IMG
    return _h

# ─────────────────────────────────────────────────────────────
#  CONVERSATION — receive first photo
# ─────────────────────────────────────────────────────────────
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    action = sess(uid).get("action", "")

    if not action:
        # No action set yet — show menu
        sess(uid).clear()
        await update.message.reply_text(
            t("choose", uid), parse_mode="MarkdownV2", reply_markup=main_menu(uid)
        )
        return ConversationHandler.END

    raw = await dl_photo(update, context)
    if not raw:
        await update.message.reply_text(t("error", uid), parse_mode="MarkdownV2")
        return ST_WAIT_IMG

    sess(uid)["img_bytes"] = raw

    # ── watermark: need text next ─────────────────────────────
    if action == "watermark":
        await update.message.reply_text(t("send_wm_text", uid), parse_mode="MarkdownV2")
        return ST_WAIT_WM_TEXT

    # ── merge: need second image ──────────────────────────────
    if action == "merge":
        await update.message.reply_text(t("send_second", uid), parse_mode="MarkdownV2")
        return ST_WAIT_SECOND_IMG

    # ── single-image actions ──────────────────────────────────
    msg = await update.message.reply_text(t("processing", uid), parse_mode="MarkdownV2")

    result = None
    try:
        if action == "removebg":
            result = await api_remove_bg(raw)

        elif action == "anime":
            await msg.edit_text(t("hf_wait", uid), parse_mode="MarkdownV2")
            result = await api_hf_anime(raw)
            if not result:
                result = make_anime_local(raw)

        elif action == "sketch":
            result = make_sketch(raw)

        elif action == "pixelart":
            result = make_pixel_art(raw)

    except Exception as e:
        log.error(f"Action {action} error: {e}")

    try:
        await msg.delete()
    except Exception:
        pass

    if result:
        await send_result(context, uid, result)
    else:
        await update.message.reply_text(t("error", uid), parse_mode="MarkdownV2")

    sessions.pop(uid, None)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
#  CONVERSATION — watermark text
# ─────────────────────────────────────────────────────────────
async def on_wm_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = (update.message.text or "").strip()
    raw  = sess(uid).get("img_bytes")
    if not raw or not text:
        await update.message.reply_text(t("error", uid), parse_mode="MarkdownV2")
        sessions.pop(uid, None)
        return ConversationHandler.END
    msg    = await update.message.reply_text(t("processing", uid), parse_mode="MarkdownV2")
    result = add_watermark(raw, text)
    await msg.delete()
    await send_result(context, uid, result)
    sessions.pop(uid, None)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
#  CONVERSATION — second image for merge
# ─────────────────────────────────────────────────────────────
async def on_second_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    raw1 = sess(uid).get("img_bytes")
    raw2 = await dl_photo(update, context)
    if not raw1 or not raw2:
        await update.message.reply_text(t("error", uid), parse_mode="MarkdownV2")
        sessions.pop(uid, None)
        return ConversationHandler.END
    msg    = await update.message.reply_text(t("processing", uid), parse_mode="MarkdownV2")
    result = merge_images(raw1, raw2)
    await msg.delete()
    await send_result(context, uid, result)
    sessions.pop(uid, None)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
#  CONVERSATION — search query text
# ─────────────────────────────────────────────────────────────
async def on_search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _do_search(update, context, update.message.text or "")

async def _do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    uid = update.effective_user.id
    if not query.strip():
        return ST_WAIT_SEARCH
    msg  = await update.message.reply_text(t("processing", uid), parse_mode="MarkdownV2")
    urls = await pexels_search(query.strip(), n=6)
    if not urls:
        url = await unsplash_random(query.strip())
        if url:
            urls = [url]
    await msg.delete()
    if not urls:
        await update.message.reply_text(
            t("no_results", uid), parse_mode="MarkdownV2", reply_markup=main_menu(uid)
        )
    else:
        for url in urls[:4]:
            try:
                await context.bot.send_photo(chat_id=uid, photo=url)
            except Exception:
                pass
        await context.bot.send_message(
            uid, t("done", uid),
            parse_mode="MarkdownV2", reply_markup=back_btn(uid)
        )
    sessions.pop(uid, None)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
#  CALLBACK QUERIES
# ─────────────────────────────────────────────────────────────
ACTION_MAP = {
    "act_removebg":  "removebg",
    "act_anime":     "anime",
    "act_sketch":    "sketch",
    "act_pixelart":  "pixelart",
    "act_watermark": "watermark",
    "act_merge":     "merge",
}

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    data = q.data
    await q.answer()

    if data == "toggle_lang":
        langs[uid] = "ar" if lang(uid) == "en" else "en"
        await q.message.edit_reply_markup(reply_markup=main_menu(uid))
        return

    if data == "show_help":
        await q.message.reply_text(t("help", uid), parse_mode="MarkdownV2")
        return

    if data == "back_menu":
        sessions.pop(uid, None)
        await q.message.reply_text(
            t("welcome", uid), parse_mode="MarkdownV2", reply_markup=main_menu(uid)
        )
        return

    if data == "act_random":
        msg = await q.message.reply_text(t("processing", uid), parse_mode="MarkdownV2")
        url = await random_image()
        await msg.delete()
        if url:
            await context.bot.send_photo(
                chat_id=uid, photo=url,
                caption=t("daily", uid), parse_mode="MarkdownV2",
                reply_markup=back_btn(uid),
            )
        return

    if data == "act_search":
        sess(uid)["action"] = "search"
        await q.message.reply_text(t("send_search", uid), parse_mode="MarkdownV2")
        return

    if data in ACTION_MAP:
        action = ACTION_MAP[data]
        sess(uid)["action"] = action
        key = "send_wm_photo" if action == "watermark" else "send_photo"
        await q.message.reply_text(t(key, uid), parse_mode="MarkdownV2")

# ─────────────────────────────────────────────────────────────
#  HEALTH CHECK SERVER
# ─────────────────────────────────────────────────────────────
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

def _health_server():
    port = int(os.getenv("PORT", 8080))
    HTTPServer(("0.0.0.0", port), _Health).serve_forever()

# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────
def main():
    threading.Thread(target=_health_server, daemon=True).start()
    log.info("Health check server started")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ── Conversation handler (handles photo + text flows) ─────
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("removebg",  _action_cmd("removebg")),
            CommandHandler("anime",     _action_cmd("anime")),
            CommandHandler("sketch",    _action_cmd("sketch")),
            CommandHandler("pixelart",  _action_cmd("pixelart")),
            CommandHandler("watermark", _action_cmd("watermark")),
            CommandHandler("merge",     _action_cmd("merge")),
            CommandHandler("search",    cmd_search),
            MessageHandler(filters.PHOTO, on_photo),
        ],
        states={
            ST_WAIT_IMG:        [MessageHandler(filters.PHOTO, on_photo)],
            ST_WAIT_SECOND_IMG: [MessageHandler(filters.PHOTO, on_second_photo)],
            ST_WAIT_WM_TEXT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, on_wm_text)],
            ST_WAIT_SEARCH:     [MessageHandler(filters.TEXT & ~filters.COMMAND, on_search_text)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("lang",   cmd_lang))
    app.add_handler(CommandHandler("random", cmd_random))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("🎨 Image Magic Bot running on Railway!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
