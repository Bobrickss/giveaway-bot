import asyncio
import logging
import random
import string
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

from config import BOT_TOKEN
from database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

COLORS = {
    "🔴 Красный":   "#E53935",
    "🔵 Синий":     "#1E88E5",
    "🟢 Зелёный":   "#43A047",
    "🟣 Фиолетовый":"#8E24AA",
    "🟠 Оранжевый": "#FB8C00",
    "⚫ Чёрный":    "#212121",
    "⚪ Белый":     "#F5F5F5",
    "🩷 Розовый":   "#E91E8C",
}

BUTTON_LABELS = [
    "🎁 Участвую!",
    "Участвую!",
    "🎯 Участвовать",
    "Участвовать",
    "💎 Принять участие",
    "Принять участие",
    "🙋 Я в деле!",
    "Я в деле!",
    "🍀 Мне повезёт!",
    "Мне повезёт!",
]

CAPTCHA_EMOJIS = ["🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯"]


class GiveawayStates(StatesGroup):
    photo = State()
    title = State()
    description = State()
    button_label = State()
    button_color = State()
    channels_tg = State()
    channels_ig = State()
    winners_count = State()
    end_time = State()
    confirm = State()
    secret_winner = State()


# ── helpers ────────────────────────────────────────────────────────────────

def make_captcha():
    answer = random.choice(CAPTCHA_EMOJIS)
    pool = random.sample([e for e in CAPTCHA_EMOJIS if e != answer], 5)
    pool.append(answer)
    random.shuffle(pool)
    return answer, pool


async def check_tg_sub(user_id: int, channel: str) -> bool:
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


def giveaway_post_text(g: dict, pcount: int) -> str:
    lines = []
    if g.get("title"):
        lines.append(f"<b>{g['title']}</b>\n")
    if g.get("description"):
        lines.append(f"{g['description']}\n")
    tg_channels = g.get("tg_channels", [])
    if tg_channels:
        lines.append("📌 <b>Обязательные подписки:</b>")
        for ch in tg_channels:
            lines.append(f"  • {ch}")
        lines.append("")
    ig = g.get("ig_username", "")
    if ig:
        lines.append(f"📸 Instagram: <a href='https://instagram.com/{ig.lstrip('@')}'>{ig}</a>\n")
    lines.append(f"🏆 Победителей: <b>{g['winners_count']}</b>")
    lines.append(f"👥 Участников: <b>{pcount}</b>")
    if g.get("end_time"):
        lines.append(f"⏰ Итоги: <b>{g['end_time']}</b>")
    return "\n".join(lines)


def participate_keyboard(giveaway_id: str, label: str, pcount: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{label} ({pcount})", callback_data=f"join_{giveaway_id}")],
        [InlineKeyboardButton(text="👥 Участники", callback_data=f"list_{giveaway_id}")]
    ])


def admin_keyboard(giveaway_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Завершить (рандом)", callback_data=f"draw_{giveaway_id}")],
        [InlineKeyboardButton(text="🎯 Назначить победителя", callback_data=f"setwinner_{giveaway_id}")],
        [InlineKeyboardButton(text="📋 Список участников", callback_data=f"adminlist_{giveaway_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{giveaway_id}")],
    ])


# ── /start ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 <b>Привет! Я бот для розыгрышей.</b>\n\n"
        "Команды:\n"
        "• /new — создать розыгрыш\n"
        "• /mygiveaways — мои розыгрыши\n"
        "• /help — инструкция",
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 <b>Как создать розыгрыш:</b>\n\n"
        "1. /new — начни создание\n"
        "2. Отправь фото для поста\n"
        "3. Введи название и описание (можно с премиум-эмодзи)\n"
        "4. Выбери текст и цвет кнопки\n"
        "5. Добавь каналы для обязательной подписки\n"
        "6. Укажи количество победителей и дату окончания\n"
        "7. Опубликуй в канале\n\n"
        "Участники проходят капчу и проверку подписок автоматически.",
        parse_mode="HTML"
    )


# ── создание розыгрыша ─────────────────────────────────────────────────────

@dp.message(Command("new"))
async def cmd_new(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await message.reply("Создавать розыгрыши можно только в личных сообщениях боту.")
        return
    await state.set_state(GiveawayStates.photo)
    await message.answer(
        "🖼 <b>Шаг 1/8 — Фото</b>\n\nОтправь главное фото для поста розыгрыша:",
        parse_mode="HTML"
    )


@dp.message(GiveawayStates.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(photo_id=file_id)
    await state.set_state(GiveawayStates.title)
    await message.answer(
        "✅ Фото сохранено!\n\n"
        "📝 <b>Шаг 2/8 — Название</b>\n\nВведи название розыгрыша\n"
        "(можно использовать премиум-эмодзи из Telegram):",
        parse_mode="HTML"
    )


@dp.message(GiveawayStates.photo)
async def process_photo_wrong(message: types.Message):
    await message.answer("⚠️ Отправь именно фотографию.")


@dp.message(GiveawayStates.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.html_text)
    await state.set_state(GiveawayStates.description)
    await message.answer(
        "📄 <b>Шаг 3/8 — Описание</b>\n\n"
        "Введи описание розыгрыша (условия, приз и т.д.)\n"
        "Поддерживаются <b>жирный</b>, <i>курсив</i> и премиум-эмодзи:",
        parse_mode="HTML"
    )


@dp.message(GiveawayStates.description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.html_text)
    await state.set_state(GiveawayStates.button_label)

    buttons = [[InlineKeyboardButton(text=label, callback_data=f"blabel_{i}")]
               for i, label in enumerate(BUTTON_LABELS)]
    await message.answer(
        "🔘 <b>Шаг 4/8 — Текст кнопки</b>\n\nВыбери текст кнопки участия\nили напиши свой:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("blabel_"))
async def cb_button_label(callback: types.CallbackQuery, state: FSMContext):
    idx = int(callback.data.split("_")[1])
    label = BUTTON_LABELS[idx]
    await state.update_data(button_label=label)
    await state.set_state(GiveawayStates.button_color)
    await show_color_picker(callback.message, state)
    await callback.answer()


@dp.message(GiveawayStates.button_label)
async def process_button_label_text(message: types.Message, state: FSMContext):
    await state.update_data(button_label=message.text)
    await state.set_state(GiveawayStates.button_color)
    await show_color_picker(message, state)


async def show_color_picker(message: types.Message, state: FSMContext):
    data = await state.get_data()
    label = data.get("button_label", "Участвовать")
    buttons = []
    for name, hex_color in COLORS.items():
        buttons.append([InlineKeyboardButton(
            text=f"{name}",
            callback_data=f"bcolor_{name}"
        )])
    await message.answer(
        f"🎨 <b>Шаг 5/8 — Цвет кнопки</b>\n\nКнопка будет называться: <b>{label}</b>\nВыбери цвет:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("bcolor_"))
async def cb_button_color(callback: types.CallbackQuery, state: FSMContext):
    color_name = callback.data[7:]
    color_hex = COLORS.get(color_name, "#1E88E5")
    await state.update_data(button_color=color_name, button_color_hex=color_hex)
    await state.set_state(GiveawayStates.channels_tg)
    await callback.message.answer(
        "📢 <b>Шаг 6/8 — Telegram каналы</b>\n\n"
        "Отправь @username каналов, на которые нужно подписаться для участия.\n"
        "Каждый канал с новой строки или по одному.\n\n"
        "Бот должен быть администратором этих каналов!\n\n"
        "Если обязательных подписок нет — нажми кнопку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Без обязательных подписок", callback_data="skip_tg_channels")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "skip_tg_channels")
async def cb_skip_tg(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(tg_channels=[])
    await state.set_state(GiveawayStates.channels_ig)
    await ask_ig(callback.message)
    await callback.answer()


@dp.message(GiveawayStates.channels_tg)
async def process_tg_channels(message: types.Message, state: FSMContext):
    channels = [c.strip() for c in message.text.replace(",", "\n").split("\n") if c.strip()]
    channels = [c if c.startswith("@") else "@" + c for c in channels]
    data = await state.get_data()
    existing = data.get("tg_channels", [])
    existing.extend(channels)
    await state.update_data(tg_channels=existing)
    await state.set_state(GiveawayStates.channels_ig)
    await message.answer(f"✅ Добавлено каналов: {len(existing)}")
    await ask_ig(message)


async def ask_ig(message: types.Message):
    await message.answer(
        "📸 <b>Шаг 7/8 — Instagram</b>\n\n"
        "Введи @username Instagram-аккаунта для обязательной подписки\n"
        "(проверка через кнопку-ссылку).\n\n"
        "Если Instagram не нужен — пропусти:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Без Instagram", callback_data="skip_ig")]
        ]),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "skip_ig")
async def cb_skip_ig(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(ig_username="")
    await state.set_state(GiveawayStates.winners_count)
    await ask_winners(callback.message)
    await callback.answer()


@dp.message(GiveawayStates.channels_ig)
async def process_ig(message: types.Message, state: FSMContext):
    ig = message.text.strip().lstrip("@")
    await state.update_data(ig_username="@" + ig)
    await state.set_state(GiveawayStates.winners_count)
    await ask_winners(message)


async def ask_winners(message: types.Message):
    btns = [[InlineKeyboardButton(text=str(i), callback_data=f"wcount_{i}") for i in range(1, 6)]]
    await message.answer(
        "🏆 <b>Шаг 8/8 — Победители и время</b>\n\nСколько победителей?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("wcount_"))
async def cb_winners_count(callback: types.CallbackQuery, state: FSMContext):
    count = int(callback.data.split("_")[1])
    await state.update_data(winners_count=count)
    await state.set_state(GiveawayStates.end_time)
    await callback.message.answer(
        "⏰ Когда завершить розыгрыш?\nВведи дату в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Или выбери быстро:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Через 1 день", callback_data="endtime_1d"),
             InlineKeyboardButton(text="Через 3 дня", callback_data="endtime_3d")],
            [InlineKeyboardButton(text="Через неделю", callback_data="endtime_7d"),
             InlineKeyboardButton(text="Без ограничений", callback_data="endtime_none")],
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("endtime_"))
async def cb_endtime(callback: types.CallbackQuery, state: FSMContext):
    val = callback.data.split("_")[1]
    from datetime import timedelta
    now = datetime.now()
    if val == "1d":
        end = (now + timedelta(days=1)).strftime("%d.%m.%Y %H:%M")
    elif val == "3d":
        end = (now + timedelta(days=3)).strftime("%d.%m.%Y %H:%M")
    elif val == "7d":
        end = (now + timedelta(days=7)).strftime("%d.%m.%Y %H:%M")
    else:
        end = ""
    await state.update_data(end_time=end)
    await finish_creation(callback.message, state, callback.from_user.id)
    await callback.answer()


@dp.message(GiveawayStates.end_time)
async def process_endtime(message: types.Message, state: FSMContext):
    await state.update_data(end_time=message.text.strip())
    await finish_creation(message, state, message.from_user.id)


async def finish_creation(message: types.Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    await state.clear()

    giveaway_id = db.create_giveaway(
        creator_id=user_id,
        title=data.get("title", ""),
        description=data.get("description", ""),
        winners_count=data.get("winners_count", 1),
        photo_id=data.get("photo_id", ""),
        button_label=data.get("button_label", "Участвовать"),
        button_color=data.get("button_color", "🔵 Синий"),
        tg_channels=data.get("tg_channels", []),
        ig_username=data.get("ig_username", ""),
        end_time=data.get("end_time", ""),
    )

    g = db.get_giveaway(giveaway_id)
    post_text = giveaway_post_text(g, 0)
    label = g["button_label"]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Опубликовать в этом чате (превью)", callback_data=f"publish_{giveaway_id}")],
        [InlineKeyboardButton(text="⚙️ Управление", callback_data=f"manage_{giveaway_id}")]
    ])

    await message.answer(
        "✅ <b>Розыгрыш создан!</b>\n\nПредпросмотр поста:",
        parse_mode="HTML"
    )

    if g.get("photo_id"):
        await message.answer_photo(
            photo=g["photo_id"],
            caption=post_text,
            reply_markup=participate_keyboard(giveaway_id, label, 0),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            post_text,
            reply_markup=participate_keyboard(giveaway_id, label, 0),
            parse_mode="HTML"
        )

    await message.answer("Управление розыгрышем:", reply_markup=kb)


# ── мои розыгрыши ──────────────────────────────────────────────────────────

@dp.message(Command("mygiveaways"))
async def cmd_mygiveaways(message: types.Message):
    if message.chat.type != "private":
        return
    giveaways = db.get_user_giveaways(message.from_user.id)
    if not giveaways:
        await message.answer("У тебя нет активных розыгрышей. Создай: /new")
        return
    buttons = []
    for g in giveaways:
        pcount = db.get_participant_count(g["id"])
        buttons.append([InlineKeyboardButton(
            text=f"🟢 {g['title'][:30]} ({pcount} уч.)",
            callback_data=f"manage_{g['id']}"
        )])
    await message.answer("📋 Твои розыгрыши:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ── управление ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("manage_"))
async def cb_manage(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    pcount = db.get_participant_count(giveaway_id)
    text = (f"⚙️ <b>{g['title']}</b>\n\n👥 Участников: {pcount}\n"
            f"🏆 Победителей: {g['winners_count']}\n"
            f"⏰ Итоги: {g.get('end_time') or 'не задано'}")
    await callback.message.answer(text, reply_markup=admin_keyboard(giveaway_id), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("publish_"))
async def cb_publish(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    post_text = giveaway_post_text(g, 0)
    label = g["button_label"]
    kb = participate_keyboard(giveaway_id, label, 0)

    if g.get("photo_id"):
        msg = await callback.message.answer_photo(
            photo=g["photo_id"],
            caption=post_text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        msg = await callback.message.answer(post_text, reply_markup=kb, parse_mode="HTML")

    db.update_giveaway_message(giveaway_id, callback.message.chat.id, msg.message_id)
    await callback.answer("✅ Опубликовано!")


# ── участие + капча + проверка подписок ────────────────────────────────────

@dp.callback_query(F.data.startswith("join_"))
async def cb_join(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)

    if not g or g["status"] != "active":
        await callback.answer("❌ Розыгрыш завершён.", show_alert=True)
        return

    user = callback.from_user
    if db.is_participant(giveaway_id, user.id):
        await callback.answer("✅ Ты уже участвуешь!", show_alert=True)
        return

    # Капча
    answer, pool = make_captcha()
    db.set_captcha(user.id, giveaway_id, answer)

    buttons = [[InlineKeyboardButton(text=e, callback_data=f"captcha_{giveaway_id}_{e}")] for e in pool]
    await callback.message.answer(
        f"🤖 <b>Проверка: ты не робот?</b>\n\nНайди и нажми: <b>{answer}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("captcha_"))
async def cb_captcha(callback: types.CallbackQuery):
    parts = callback.data.split("_", 2)
    giveaway_id = parts[1]
    chosen = parts[2]
    user_id = callback.from_user.id

    expected = db.get_captcha(user_id, giveaway_id)
    if not expected:
        await callback.answer("Сессия устарела. Попробуй снова.", show_alert=True)
        return

    if chosen != expected:
        await callback.answer("❌ Неверно! Попробуй ещё раз.", show_alert=True)
        return

    db.clear_captcha(user_id, giveaway_id)
    g = db.get_giveaway(giveaway_id)

    # Проверка Telegram подписок
    tg_channels = g.get("tg_channels", [])
    not_subbed = []
    for ch in tg_channels:
        ok = await check_tg_sub(user_id, ch)
        if not ok:
            not_subbed.append(ch)

    if not_subbed:
        buttons = [[InlineKeyboardButton(text=f"📢 Подписаться: {ch}", url=f"https://t.me/{ch.lstrip('@')}")]
                   for ch in not_subbed]
        buttons.append([InlineKeyboardButton(text="✅ Я подписался", callback_data=f"checksubscribe_{giveaway_id}")])
        await callback.message.answer(
            "⚠️ <b>Необходимо подписаться на каналы:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    await register_participant(callback, giveaway_id, g)


@dp.callback_query(F.data.startswith("checksubscribe_"))
async def cb_check_subscribe(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    user_id = callback.from_user.id

    tg_channels = g.get("tg_channels", [])
    not_subbed = []
    for ch in tg_channels:
        ok = await check_tg_sub(user_id, ch)
        if not ok:
            not_subbed.append(ch)

    if not_subbed:
        await callback.answer("❌ Ты ещё не подписан на все каналы!", show_alert=True)
        return

    if db.is_participant(giveaway_id, user_id):
        await callback.answer("✅ Ты уже участвуешь!", show_alert=True)
        return

    await register_participant(callback, giveaway_id, g)


async def register_participant(callback: types.CallbackQuery, giveaway_id: str, g: dict):
    user = callback.from_user
    ig = g.get("ig_username", "")

    # Если есть Instagram — показываем кнопку и сразу регистрируем (проверить нельзя автоматически)
    db.add_participant(giveaway_id, user.id, user.username or user.first_name)
    pcount = db.get_participant_count(giveaway_id)

    # Красивый экран участия
    ig_line = f"\n📸 Не забудь подписаться в Instagram: {ig}" if ig else ""
    success_text = (
        f"🎉 <b>Ты в розыгрыше!</b>\n\n"
        f"<b>{g['title']}</b>\n\n"
        f"👤 Участник #{pcount}{ig_line}\n\n"
        f"🍀 Удачи! Итоги: {g.get('end_time') or 'по решению организатора'}"
    )

    extra_buttons = []
    if ig:
        extra_buttons.append([InlineKeyboardButton(
            text=f"📸 Подписаться в Instagram",
            url=f"https://instagram.com/{ig.lstrip('@')}"
        )])

    await callback.message.answer(
        success_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=extra_buttons) if extra_buttons else None,
        parse_mode="HTML"
    )

    # Обновляем счётчик в посте
    try:
        post_info = db.get_giveaway_post(giveaway_id)
        if post_info:
            label = g["button_label"]
            new_kb = participate_keyboard(giveaway_id, label, pcount)
            post_text = giveaway_post_text(g, pcount)
            if g.get("photo_id"):
                await bot.edit_message_caption(
                    chat_id=post_info["chat_id"],
                    message_id=post_info["message_id"],
                    caption=post_text,
                    reply_markup=new_kb,
                    parse_mode="HTML"
                )
            else:
                await bot.edit_message_text(
                    chat_id=post_info["chat_id"],
                    message_id=post_info["message_id"],
                    text=post_text,
                    reply_markup=new_kb,
                    parse_mode="HTML"
                )
    except TelegramBadRequest:
        pass

    await callback.answer()


# ── список участников ──────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("list_"))
async def cb_list(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    participants = db.get_participants(giveaway_id)
    if not participants:
        await callback.answer("Участников пока нет.", show_alert=True)
        return
    lines = [f"{i+1}. {p['username']}" for i, p in enumerate(participants[:50])]
    text = "👥 <b>Участники:</b>\n" + "\n".join(lines)
    if len(participants) > 50:
        text += f"\n...ещё {len(participants)-50}"
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data.startswith("adminlist_"))
async def cb_adminlist(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    participants = db.get_participants(giveaway_id)
    secret_id = db.get_secret_winner(giveaway_id)
    if not participants:
        await callback.answer("Участников нет.", show_alert=True)
        return
    lines = []
    for i, p in enumerate(participants):
        mark = " 🎯" if p["user_id"] == secret_id else ""
        lines.append(f"{i+1}. {p['username']}{mark}")
    text = f"👥 <b>Участники ({len(participants)}):</b>\n" + "\n".join(lines[:100])
    if secret_id:
        text += "\n\n🎯 — скрытно назначен победителем"
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")


# ── назначить победителя скрытно ───────────────────────────────────────────

@dp.callback_query(F.data.startswith("setwinner_"))
async def cb_setwinner(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    participants = db.get_participants(giveaway_id)
    if not participants:
        await callback.answer("Нет участников!", show_alert=True)
        return
    buttons = [[InlineKeyboardButton(
        text=p["username"],
        callback_data=f"confirmwinner_{giveaway_id}_{p['user_id']}"
    )] for p in participants]
    buttons.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"manage_{giveaway_id}")])
    await callback.message.answer(
        "🎯 Выбери нужного победителя (скрытно):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("confirmwinner_"))
async def cb_confirmwinner(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    giveaway_id = parts[1]
    winner_id = int(parts[2])
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    db.set_secret_winner(giveaway_id, winner_id)
    winner = db.get_participant_by_id(giveaway_id, winner_id)
    name = winner["username"] if winner else str(winner_id)
    await callback.message.answer(
        f"✅ Победитель назначен скрытно: <b>{name}</b>\n\nТеперь нажми «Завершить» — результат выглядит как честный рандом.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Завершить", callback_data=f"draw_{giveaway_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


# ── жеребьёвка ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("draw_"))
async def cb_draw(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    if g["status"] != "active":
        await callback.answer("Уже завершён.", show_alert=True)
        return
    participants = db.get_participants(giveaway_id)
    if not participants:
        await callback.answer("Нет участников!", show_alert=True)
        return

    secret_id = db.get_secret_winner(giveaway_id)
    wcount = min(g["winners_count"], len(participants))

    if secret_id:
        secret = next((p for p in participants if p["user_id"] == secret_id), None)
        others = [p for p in participants if p["user_id"] != secret_id]
        random.shuffle(others)
        winners = ([secret] + others[:wcount - 1]) if secret else others[:wcount]
    else:
        pool = participants[:]
        random.shuffle(pool)
        winners = pool[:wcount]

    db.finish_giveaway(giveaway_id, [w["user_id"] for w in winners])

    winner_lines = "\n".join(f"🥇 {w['username']}" for w in winners)
    result = (
        f"🎉 <b>Розыгрыш завершён!</b>\n\n"
        f"<b>{g['title']}</b>\n\n"
        f"🏆 {'Победитель' if len(winners)==1 else 'Победители'}:\n{winner_lines}\n\n"
        f"👥 Участвовало: {len(participants)}\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    if g.get("photo_id"):
        await callback.message.answer_photo(photo=g["photo_id"], caption=result, parse_mode="HTML")
    else:
        await callback.message.answer(result, parse_mode="HTML")

    post_info = db.get_giveaway_post(giveaway_id)
    if post_info:
        try:
            if g.get("photo_id"):
                await bot.edit_message_caption(
                    chat_id=post_info["chat_id"],
                    message_id=post_info["message_id"],
                    caption=result + "\n\n✅ <i>Розыгрыш завершён</i>",
                    parse_mode="HTML"
                )
            else:
                await bot.edit_message_text(
                    chat_id=post_info["chat_id"],
                    message_id=post_info["message_id"],
                    text=result + "\n\n✅ <i>Розыгрыш завершён</i>",
                    parse_mode="HTML"
                )
        except Exception:
            pass

    await callback.answer("✅ Завершено!")


# ── отмена ─────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await callback.message.answer(
        "⚠️ Отменить розыгрыш?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"cancelok_{giveaway_id}"),
             InlineKeyboardButton(text="↩️ Нет", callback_data=f"manage_{giveaway_id}")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancelok_"))
async def cb_cancelok(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    db.cancel_giveaway(giveaway_id)
    await callback.message.edit_text("❌ Розыгрыш отменён.")
    await callback.answer()


# ── запуск ─────────────────────────────────────────────────────────────────

async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
