import json
import os
import asyncio
import uuid
import requests
from datetime import datetime, date, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Update
from aiogram.filters import Command
from aiogram import F
from aiogram.client.session.aiohttp import AiohttpSession

# ==================== НАСТРОЙКИ (ЗАМЕНИ НА СВОИ) ====================
TOKEN = "8925937134:AAG_xQQxj2GDUldtCRuR7ie0zGfImA6q6Pk"
CHANNEL_ID = -1003745006151
ADMIN_ID = 1584577191

# ЮKassa (вставь свои ключи, когда получишь)
SHOP_ID = "1359471"          # Например "123456"
SECRET_KEY = "live_hR54U-Pa1-vMIJh6bnQxzWxYrB6WXQ4PdjqAPfbaovo"       # Например "live_xxxxxxxx" или "test_xxxxxxxx"

# Прокси (оставь пустым, если не нужен)
PROXY = ""  # Например "socks5://185.252.120.34:1080"

# Webhook настройки
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_HOST = "https://png-bot.onrender.com"
# ===================================================================

# Создаём сессию с прокси (если указан)
if PROXY:
    session = AiohttpSession(proxy=PROXY)
    bot = Bot(token=TOKEN, session=session)
else:
    bot = Bot(token=TOKEN)

dp = Dispatcher()

# ==================== РАБОТА С БАЗАМИ ДАННЫХ ====================
DB_FILE = "db.json"
USERS_FILE = "users.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

db = load_db()
users = load_users()
user_state = {}
pending_payments = {}

DAILY_FREE_LIMIT = 20

# ==================== ЛОГИКА ПОДПИСОК И ЛИМИТОВ ====================
def can_user_search(user_id):
    today = date.today().isoformat()
    user_id_str = str(user_id)
    
    if user_id_str not in users:
        users[user_id_str] = {
            "subscription_until": None,
            "searches_today": 0,
            "last_search_date": today
        }
        save_users(users)
    
    user = users[user_id_str]
    
    if user["subscription_until"]:
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            return True, "premium"
    
    if user["last_search_date"] != today:
        user["searches_today"] = 0
        user["last_search_date"] = today
        save_users(users)
    
    if user["searches_today"] < DAILY_FREE_LIMIT:
        return True, "free"
    return False, "free"

def increment_search(user_id):
    today = date.today().isoformat()
    user_id_str = str(user_id)
    
    if user_id_str not in users:
        users[user_id_str] = {
            "subscription_until": None,
            "searches_today": 0,
            "last_search_date": today
        }
    
    user = users[user_id_str]
    
    if user.get("subscription_until"):
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            save_users(users)
            return
    
    if user.get("last_search_date") != today:
        user["searches_today"] = 0
        user["last_search_date"] = today
    
    user["searches_today"] = user.get("searches_today", 0) + 1
    users[user_id_str] = user
    save_users(users)

def get_remaining_searches(user_id):
    today = date.today().isoformat()
    user_id_str = str(user_id)
    if user_id_str not in users:
        return DAILY_FREE_LIMIT
    user = users[user_id_str]
    if user.get("subscription_until"):
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            return float('inf')
    if user.get("last_search_date") != today:
        return DAILY_FREE_LIMIT
    used = user.get("searches_today", 0)
    return max(0, DAILY_FREE_LIMIT - used)

# ==================== ПОИСК ПО КЛЮЧЕВЫМ СЛОВАМ ====================
def search_by_keywords(query):
    query_words = set(query.lower().split())
    results = []
    for keyword_phrase, msg_ids in db.items():
        phrase_words = set(keyword_phrase.lower().split())
        if query_words.issubset(phrase_words):
            results.extend(msg_ids)
    return list(dict.fromkeys(results))

# ==================== ЮKassa (ПЛАТЕЖИ) ====================
def create_payment(amount=30, description="Premium access for 30 days"):
    if not SHOP_ID or not SECRET_KEY:
        return None, None
    
    idempotence_key = str(uuid.uuid4())
    payment_data = {
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
        "payment_method_data": {"type": "bank_card"},
        "confirmation": {"type": "redirect", "return_url": "https://t.me"},
        "description": description,
        "capture": True
    }
    auth = (SHOP_ID, SECRET_KEY)
    response = requests.post(
        "https://api.yookassa.ru/v3/payments",
        json=payment_data,
        auth=auth,
        headers={"Idempotence-Key": idempotence_key}
    )
    if response.status_code == 200:
        data = response.json()
        return data["confirmation"]["confirmation_url"], data["id"]
    return None, None

def check_payment_status(payment_id):
    if not SHOP_ID or not SECRET_KEY:
        return None
    
    auth = (SHOP_ID, SECRET_KEY)
    response = requests.get(
        f"https://api.yookassa.ru/v3/payments/{payment_id}",
        auth=auth
    )
    if response.status_code == 200:
        return response.json().get("status")
    return None

# ==================== ОБРАБОТЧИКИ КОМАНД И КНОПОК ====================
@dp.message(Command("start"))
async def start(message: types.Message):
    remaining = get_remaining_searches(message.from_user.id)
    user = users.get(str(message.from_user.id), {})
    is_premium = False
    if user.get("subscription_until"):
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            is_premium = True
    
    status_text = "⭐ Premium — unlimited" if is_premium else f"🆓 Free — {remaining} of {DAILY_FREE_LIMIT} today"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Buy premium (30 RUB)", callback_data="buy_premium")],
        [InlineKeyboardButton(text="📖 How to use", callback_data="help")]
    ])
    
    await message.answer(
        f"🐱 Hello, {message.from_user.first_name}!\n\n"
        f"I search PNG without background. Send a word or phrase.\n\n"
        f"{status_text}\n\n"
        f"📌 One request = one image. Browsing is free!\n\n"
        f"⬇️ Send your request",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "help")
async def show_help(callback: types.CallbackQuery):
    await callback.message.answer(
        "📖 **How to use:**\n\n"
        "1️⃣ Send a word or phrase\n"
        "2️⃣ Get the first image (costs 1 request)\n"
        "3️⃣ Use Next/Back buttons to browse (free)\n\n"
        f"💰 {DAILY_FREE_LIMIT} free requests per day\n"
        "💎 Premium — 30 RUB/month, unlimited"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "buy_premium")
async def buy_premium(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if SHOP_ID and SECRET_KEY:
        payment_url, payment_id = create_payment()
        
        if payment_url and payment_id:
            pending_payments[user_id] = payment_id
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Pay 30 RUB", url=payment_url)],
                [InlineKeyboardButton(text="✅ Check payment", callback_data="check_payment")]
            ])
            
            await callback.message.answer(
                "💎 **Premium — 30 RUB/month**\n\n"
                "Click the button to pay by card or SBP.\n"
                "After payment, click 'Check payment' to activate.",
                reply_markup=keyboard
            )
            await callback.answer()
            return
    
    # Заглушка, если нет ключей ЮKassa
    await callback.message.answer(
        "💎 Premium — 30 RUB/month\n\n"
        "Payment system is being configured.\n"
        "Contact @admin to activate manually."
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "check_payment")
async def check_payment(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in pending_payments:
        await callback.message.answer("No active payments. Use /start to buy premium.")
        await callback.answer()
        return
    
    payment_id = pending_payments[user_id]
    status = check_payment_status(payment_id)
    
    if status == "succeeded":
        expiry_date = (date.today() + timedelta(days=30)).isoformat()
        users[str(user_id)] = users.get(str(user_id), {})
        users[str(user_id)]["subscription_until"] = expiry_date
        save_users(users)
        del pending_payments[user_id]
        
        await callback.message.answer("🎉 Premium activated! Unlimited access for 30 days.")
        await bot.send_message(ADMIN_ID, f"✅ Payment received from @{callback.from_user.username} (ID: {user_id})")
    elif status in ["pending", "waiting_for_capture"]:
        await callback.message.answer("⏳ Payment not completed yet. Try again in a minute.")
    else:
        await callback.message.answer("❌ Payment not found. Try again.")
    
    await callback.answer()

@dp.message(Command("activate"))
async def activate_subscription(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ No permission")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Usage: /activate USER_ID")
        return
    
    user_id = parts[1]
    expiry_date = (date.today() + timedelta(days=30)).isoformat()
    
    if user_id not in users:
        users[user_id] = {}
    
    users[user_id]["subscription_until"] = expiry_date
    save_users(users)
    
    await message.answer(f"✅ Subscription activated for {user_id} until {expiry_date}")
    
    try:
        await bot.send_message(int(user_id), "🎉 Premium activated by admin!")
    except:
        pass

@dp.message(Command("check"))
async def check_status(message: types.Message):
    remaining = get_remaining_searches(message.from_user.id)
    user = users.get(str(message.from_user.id), {})
    
    if user.get("subscription_until"):
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            await message.answer(f"⭐ Premium until {expiry}")
            return
    
    await message.answer(f"🆓 Free: {remaining} requests left today")

@dp.channel_post()
async def on_channel_post(message: types.Message):
    if message.document and message.document.mime_type == "image/png":
        msg_id = message.message_id
        caption = message.caption or ""
        
        if not caption.strip():
            await message.reply("⚠️ Error: add caption to file")
            return
        
        keyword = caption.strip().lower()
        
        if keyword not in db:
            db[keyword] = []
        if msg_id not in db[keyword]:
            db[keyword].append(msg_id)
        
        save_db(db)
        await message.reply(f"✅ Saved: '{keyword}'")

@dp.message(F.text)
async def search(message: types.Message):
    user_id = message.from_user.id
    query = message.text.lower().strip()
    
    can_search, tier = can_user_search(user_id)
    
    if not can_search:
        await message.answer(
            f"❌ Daily limit ({DAILY_FREE_LIMIT}) reached.\n\n"
            f"💎 Buy premium for unlimited access!\n"
            f"Send /start and click 'Buy premium'"
        )
        return
    
    results = search_by_keywords(query)
    
    if not results:
        await message.answer(f"❌ Nothing found for '{query}'\n\nTry other words.")
        return
    
    increment_search(user_id)
    remaining = get_remaining_searches(user_id)
    
    user_state[user_id] = {
        "word": query,
        "index": 0,
        "total": len(results),
        "results": results
    }
    
    limit_text = f"Requests left: {remaining}" if tier == "free" else "Premium — unlimited"
    await message.answer(f"🔍 Found {len(results)} images\n{limit_text}")
    await send_image(user_id, message.chat.id)

async def send_image(user_id, chat_id):
    state = user_state.get(user_id)
    if not state:
        return
    
    msg_id = state["results"][state["index"]]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="◀️ Back", callback_data="prev"),
            InlineKeyboardButton(text=f"{state['index']+1}/{state['total']}", callback_data="none"),
            InlineKeyboardButton(text="Next ▶️", callback_data="next")
        ],
        [InlineKeyboardButton(text="💎 Buy premium", callback_data="buy_premium")]
    ])
    
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=CHANNEL_ID,
            message_id=msg_id,
            reply_markup=keyboard
        )
    except Exception as e:
        await bot.send_message(chat_id, f"Error: {e}")

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data
    
    if action in ["help", "buy_premium", "check_payment", "none"]:
        await callback.answer()
        return
    
    state = user_state.get(user_id)
    if not state:
        await callback.message.answer("🔍 Send a word first")
        await callback.answer()
        return
    
    if action == "prev":
        state["index"] = (state["index"] - 1) % state["total"]
    elif action == "next":
        state["index"] = (state["index"] + 1) % state["total"]
    else:
        await callback.answer()
        return
    
    user_state[user_id] = state
    await callback.message.delete()
    await send_image(user_id, callback.message.chat.id)
    await callback.answer()

# ==================== ЗАПУСК В РЕЖИМЕ WEBHOOK ====================
async def on_startup():
    webhook_url = f"{WEBHOOK_HOST}/webhook"
    await bot.set_webhook(webhook_url)
    print(f"✅ Webhook set to {webhook_url}")

async def handle_webhook(request):
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return web.Response()

async def main():
    app = web.Application()
    app.router.post("/webhook", handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    await on_startup()
    print(f"🚀 Bot started on port {PORT}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())