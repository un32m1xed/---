import json
import os
from datetime import datetime, date, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram import F
from aiogram.types import Update
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import web
import asyncio
import uuid
import requests

# ===== НАСТРОЙКИ (ТВОИ ДАННЫЕ УЖЕ ВСТАВЛЕНЫ) =====
TOKEN = "8925937134:AAG_xQQxj2GDUldtCRuR7ie0zGfImA6q6Pk"
CHANNEL_ID = -1003745006151
ADMIN_ID = 1584577191
SHOP_ID = ""
SECRET_KEY = ""

# Прокси (рабочий SOCKS5 прокси, может потребоваться замена)
PROXY = "socks5://185.252.120.34:1080"
# ================================================

# --- НАСТРОЙКИ WEBHOOKА (НЕ ТРОГАЙ) ---
PORT = int(os.environ.get('PORT', 10000))
WEBHOOK_HOST = f"https://png-bot.onrender.com" # Твой URL на Render

# --- СОЗДАНИЕ БОТА С ПРОКСИ ---
session = AiohttpSession(proxy=PROXY)
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# --- ОСТАЛЬНАЯ ЧАСТЬ ТВОЕГО КОДА (ФУНКЦИИ) ---
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

def search_by_keywords(query):
    query_words = set(query.lower().split())
    results = []
    for keyword_phrase, msg_ids in db.items():
        phrase_words = set(keyword_phrase.lower().split())
        if query_words.issubset(phrase_words):
            results.extend(msg_ids)
    return list(dict.fromkeys(results))

def create_payment(amount=30, description="Премиум-доступ на месяц"):
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
    auth = (SHOP_ID, SECRET_KEY)
    response = requests.get(
        f"https://api.yookassa.ru/v3/payments/{payment_id}",
        auth=auth
    )
    if response.status_code == 200:
        return response.json().get("status")
    return None

@dp.message(Command("start"))
async def start(message: types.Message):
    remaining = get_remaining_searches(message.from_user.id)
    user = users.get(str(message.from_user.id), {})
    is_premium = False
    if user.get("subscription_until"):
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            is_premium = True
    
    status_text = "⭐ Премиум — безлимит" if is_premium else f"🆓 Бесплатно — {remaining} из {DAILY_FREE_LIMIT} запросов сегодня"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Купить премиум (30₽)", callback_data="buy_premium")],
        [InlineKeyboardButton(text="📖 Как пользоваться", callback_data="help")]
    ])
    
    await message.answer(
        f"🐱 Привет, {message.from_user.first_name}!\n\n"
        f"Я ищу PNG без фона. Напиши слово или фразу.\n\n"
        f"{status_text}\n\n"
        f"📌 Один запрос = одна картинка. Листай кнопками бесплатно!\n\n"
        f"⬇️ Напиши свой запрос",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "help")
async def show_help(callback: types.CallbackQuery):
    await callback.message.answer(
        "📖 **Инструкция:**\n\n"
        "1️⃣ Напиши слово или фразу\n"
        "2️⃣ Получишь первую картинку (снимает 1 запрос)\n"
        "3️⃣ Кнопки Вперёд/Назад — бесплатно\n\n"
        "💰 20 запросов бесплатно в день\n"
        "💎 Премиум — 30₽/месяц, безлимит"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "buy_premium")
async def buy_premium(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Временное сообщение для теста (пока нет ЮKassa)
    await callback.message.answer(
        "💎 **Премиум-доступ — 30₽/месяц**\n\n"
        "Сейчас тестовый режим. Чтобы активировать премиум вручную, напиши админу.\n\n"
        "После настройки ЮKassa здесь будет автоматическая оплата.",
        parse_mode="Markdown"
    )
    await callback.answer()
    
    # Уведомление админу
    await bot.send_message(
        ADMIN_ID,
        f"💰 Пользователь @{callback.from_user.username} (ID: {user_id}) хочет купить премиум!"
    )

@dp.message(Command("activate"))
async def activate_subscription(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет прав")
        return
    
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /activate USER_ID")
        return
    
    user_id = parts[1]
    expiry_date = (date.today() + timedelta(days=30)).isoformat()
    
    if user_id not in users:
        users[user_id] = {}
    
    users[user_id]["subscription_until"] = expiry_date
    save_users(users)
    
    await message.answer(f"✅ Подписка активирована для {user_id} до {expiry_date}")
    
    try:
        await bot.send_message(int(user_id), "🎉 Премиум активирован вручную админом.")
    except:
        pass

@dp.message(Command("check"))
async def check_status(message: types.Message):
    remaining = get_remaining_searches(message.from_user.id)
    user = users.get(str(message.from_user.id), {})
    
    if user.get("subscription_until"):
        expiry = datetime.fromisoformat(user["subscription_until"]).date()
        if expiry >= date.today():
            await message.answer(f"⭐ Премиум до {expiry}")
            return
    
    await message.answer(f"🆓 Сегодня осталось запросов: {remaining} из {DAILY_FREE_LIMIT}")

@dp.channel_post()
async def on_channel_post(message: types.Message):
    if message.document and message.document.mime_type == "image/png":
        msg_id = message.message_id
        caption = message.caption or ""
        
        if not caption.strip():
            await message.reply("⚠️ Ошибка: добавь подпись к файлу")
            return
        
        keyword = caption.strip().lower()
        
        if keyword not in db:
            db[keyword] = []
        if msg_id not in db[keyword]:
            db[keyword].append(msg_id)
        
        save_db(db)
        await message.reply(f"✅ Сохранено по запросу: «{keyword}»")

@dp.message(F.text)
async def search(message: types.Message):
    user_id = message.from_user.id
    query = message.text.lower().strip()
    
    can_search, tier = can_user_search(user_id)
    
    if not can_search:
        await message.answer(
            f"❌ Лимит {DAILY_FREE_LIMIT} запросов на сегодня исчерпан.\n\n"
            f"Листать найденное кнопками — бесплатно!\n\n"
            f"💎 Купи премиум за 30₽/месяц — безлимит!\n"
            f"Напиши /start и нажми «Купить премиум»"
        )
        return
    
    results = search_by_keywords(query)
    
    if not results:
        await message.answer(f"❌ Ничего не найдено для «{query}»\n\nПопробуй другие слова.")
        return
    
    increment_search(user_id)
    remaining = get_remaining_searches(user_id)
    
    user_state[user_id] = {
        "word": query,
        "index": 0,
        "total": len(results),
        "results": results
    }
    
    limit_text = f"Осталось запросов: {remaining}" if tier == "free" else "Премиум — безлимит"
    await message.answer(f"🔍 Найдено {len(results)} картинок\n{limit_text}")
    await send_image(user_id, message.chat.id)

async def send_image(user_id, chat_id):
    state = user_state.get(user_id)
    if not state:
        return
    
    msg_id = state["results"][state["index"]]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="prev"),
            InlineKeyboardButton(text=f"{state['index']+1}/{state['total']}", callback_data="none"),
            InlineKeyboardButton(text="Вперёд ▶️", callback_data="next")
        ],
        [InlineKeyboardButton(text="💎 Купить премиум", callback_data="buy_premium")]
    ])
    
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=CHANNEL_ID,
            message_id=msg_id,
            reply_markup=keyboard
        )
    except Exception as e:
        await bot.send_message(chat_id, f"Ошибка: {e}")

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data
    
    # Пропускаем специальные кнопки
    if action in ["help", "buy_premium", "none"]:
        await callback.answer()
        return
    
    state = user_state.get(user_id)
    if not state:
        await callback.message.answer("🔍 Сначала напиши слово для поиска")
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

# --- НОВАЯ ЧАСТЬ: ЗАПУСК В РЕЖИМЕ WEBHOOK ---
async def on_startup():
    """Устанавливает webhook при запуске"""
    webhook_url = f"{WEBHOOK_HOST}/webhook"
    await bot.set_webhook(webhook_url)
    print(f"✅ Webhook установлен на {webhook_url}")

async def handle_webhook(request):
    """Обрабатывает входящие запросы от Telegram"""
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return web.Response()

async def main():
    """Запускает aiohttp веб-сервер"""
    app = web.Application()
    app.router.post("/webhook", handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    await on_startup()
    print(f"🚀 Бот запущен в режиме webhook на порту {PORT}")
    
    # Бесконечно ждем
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())