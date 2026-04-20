import asyncio
import logging
import random
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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

# Имя бота (заполняется при старте)
BOT_USERNAME = None

BUTTON_LABELS = [
    "🎁 Участвую!", "Участвую!", "🎯 Участвовать", "Участвовать",
    "💎 Принять участие", "Принять участие", "🙋 Я в деле!",
    "Я в деле!", "🍀 Мне повезёт!", "Мне повезёт!",
]

# Цвета — применяются к оформлению поста (Telegram не позволяет красить кнопки через API)
COLORS = {
    "🔴 Красный":    ("🔴", "#E53935"),
    "🔵 Синий":      ("🔵", "#1E88E5"),
    "🟢 Зелёный":    ("🟢", "#43A047"),
    "🟣 Фиолетовый": ("🟣", "#8E24AA"),
    "🟠 Оранжевый":  ("🟠", "#FB8C00"),
    "⚫ Чёрный":     ("⚫", "#212121"),
    "🩷 Розовый":    ("🩷", "#E91E8C"),
    "🟡 Жёлтый":    ("🟡", "#F9A825"),
}

CAPTCHA_EMOJIS = ["🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯","🦁","🐮","🐷","🐸","🐵"]


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


async def check_tg_sub(user_id, channel):
    try:
        m = await bot.get_chat_member(channel, user_id)
        return m.status not in ("left", "kicked", "banned")
    except Exception:
        return False


def color_circle(color_name):
    """Возвращает эмодзи-круг для выбранного цвета."""
    return COLORS.get(color_name, ("🔵", ""))[0]


def giveaway_post_text(g, pcount):
    """Формирует текст поста. Цвет отражается эмодзи-кругом рядом с заголовком."""
    circle = color_circle(g.get("button_color", "🔵 Синий"))
    lines = []
    if g.get("title"):
        lines.append(f"{circle} <b>{g['title']}</b>\n")
    if g.get("description"):
        lines.append(f"{g['description']}\n")
    tg_ch = g.get("tg_channels", [])
    if tg_ch:
        lines.append("📌 <b>Обязательные подписки:</b>")
        for ch in tg_ch:
            lines.append(f"  • {ch}")
        lines.append("")
    ig = g.get("ig_username", "")
    if ig:
        lines.append(f"📸 Instagram: <a href='https://instagram.com/{ig.lstrip(chr(64))}'>{ig}</a>\n")
    lines.append(f"🏆 Победителей: <b>{g['winners_count']}</b>")
    lines.append(f"👥 Участников: <b>{pcount}</b>")
    if g.get("end_time"):
        lines.append(f"⏰ Итоги: <b>{g['end_time']}</b>")
    return "\n".join(lines)


def participate_kb(giveaway_id, label):
    """
    Кнопка участия использует deep link — работает в канале, при пересылке, везде.
    При нажатии Telegram открывает личку с ботом и передаёт start=join_XXX.
    """
    global BOT_USERNAME
    url = f"https://t.me/{BOT_USERNAME}?start=join_{giveaway_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, url=url)]
    ])


def admin_kb(giveaway_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Опубликовать в канал", callback_data=f"publish_{giveaway_id}")],
        [InlineKeyboardButton(text="🎲 Завершить (рандом)", callback_data=f"draw_{giveaway_id}")],
        [InlineKeyboardButton(text="🎯 Назначить победителя скрытно", callback_data=f"setwinner_{giveaway_id}")],
        [InlineKeyboardButton(text="📋 Список участников", callback_data=f"adminlist_{giveaway_id}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{giveaway_id}")],
    ])


def captcha_inline_kb(giveaway_id, answer):
    """5 кнопок-эмодзи для капчи прямо в боте."""
    pool = random.sample(CAPTCHA_EMOJIS, 5)
    if answer not in pool:
        pool[random.randint(0, 4)] = answer
    random.shuffle(pool)
    buttons = [
        InlineKeyboardButton(text=e, callback_data=f"captcha_{giveaway_id}_{answer}_{e}")
        for e in pool
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


# ──────────────────────────────────────────────
# /start — обрабатывает deep link join_XXX
# ──────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    param = args[1] if len(args) > 1 else ""

    if param.startswith("join_"):
        gid = param[5:]
        await handle_join(message, gid)
        return

    await message.answer(
        "👋 <b>Привет! Я бот для розыгрышей.</b>\n\n"
        "/new — создать розыгрыш\n"
        "/mygiveaways — мои розыгрыши\n"
        "/help — инструкция",
        parse_mode="HTML"
    )


async def handle_join(message: types.Message, gid: str):
    """Логика участия — вызывается через deep link."""
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        await message.answer("❌ Розыгрыш завершён или не найден.")
        return
    user = message.from_user
    if db.is_participant(gid, user.id):
        await message.answer("✅ Ты уже участвуешь в этом розыгрыше!")
        return
    answer = random.choice(CAPTCHA_EMOJIS)
    db.set_captcha(user.id, gid, answer)
    await message.answer(
        f"🤖 <b>Проверка на человечность</b>\n\nНайди и нажми: {answer}",
        reply_markup=captcha_inline_kb(gid, answer),
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📖 <b>Как создать розыгрыш:</b>\n\n"
        "1. /new → фото, название, описание\n"
        "2. Выбери текст и цвет кнопки\n"
        "3. Добавь каналы для обязательной подписки\n"
        "4. Укажи победителей и дату\n"
        "5. Нажми «Опубликовать в канал» — выбери канал из списка\n\n"
        "Кнопка участия работает в канале, при пересылке поста — везде.\n"
        "Участники проходят проверку прямо в личке с ботом.",
        parse_mode="HTML"
    )


# ──────────────────────────────────────────────
# Создание розыгрыша
# ──────────────────────────────────────────────
@dp.message(Command("new"))
async def cmd_new(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await message.reply("Создавай розыгрыши в личных сообщениях.")
        return
    await state.set_state(GiveawayStates.photo)
    await message.answer("🖼 <b>Шаг 1/8 — Фото</b>\n\nОтправь главное фото для поста:", parse_mode="HTML")


@dp.message(GiveawayStates.photo, F.photo)
async def process_photo(message: types.Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await state.set_state(GiveawayStates.title)
    await message.answer("✅ Фото!\n\n📝 <b>Шаг 2/8 — Название</b>\n\nВведи название:", parse_mode="HTML")


@dp.message(GiveawayStates.photo)
async def photo_wrong(message: types.Message):
    await message.answer("⚠️ Отправь фотографию.")


@dp.message(GiveawayStates.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.html_text)
    await state.set_state(GiveawayStates.description)
    await message.answer("📄 <b>Шаг 3/8 — Описание</b>\n\nВведи описание (условия, приз):", parse_mode="HTML")


@dp.message(GiveawayStates.description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.html_text)
    await state.set_state(GiveawayStates.button_label)
    btns = [[InlineKeyboardButton(text=l, callback_data=f"blabel_{i}")] for i, l in enumerate(BUTTON_LABELS)]
    await message.answer(
        "🔘 <b>Шаг 4/8 — Текст кнопки</b>\n\nВыбери или напиши свой:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("blabel_"))
async def cb_blabel(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(button_label=BUTTON_LABELS[int(callback.data.split("_")[1])])
    await state.set_state(GiveawayStates.button_color)
    await show_color_picker(callback.message, state)
    await callback.answer()


@dp.message(GiveawayStates.button_label)
async def process_blabel_text(message: types.Message, state: FSMContext):
    await state.update_data(button_label=message.text)
    await state.set_state(GiveawayStates.button_color)
    await show_color_picker(message, state)


async def show_color_picker(message, state):
    data = await state.get_data()
    btns = [
        [InlineKeyboardButton(text=f"{COLORS[n][0]} {n.split()[-1]}", callback_data=f"bcolor_{n}")]
        for n in COLORS
    ]
    await message.answer(
        f"🎨 <b>Шаг 5/8 — Цвет оформления</b>\n\n"
        f"Кнопка: <b>{data.get('button_label', 'Участвовать')}</b>\n\n"
        f"Выбранный цвет отображается эмодзи-кругом рядом с заголовком поста:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("bcolor_"))
async def cb_bcolor(callback: types.CallbackQuery, state: FSMContext):
    color = callback.data[7:]
    await state.update_data(button_color=color)
    circle = color_circle(color)
    await callback.answer(f"Выбран цвет {circle}")
    await state.set_state(GiveawayStates.channels_tg)
    await callback.message.answer(
        "📢 <b>Шаг 6/8 — Telegram каналы</b>\n\n"
        "Отправь @username каналов для обязательной подписки.\n"
        "Бот должен быть администратором!\n\n"
        "Если не нужно — пропусти:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Без подписок", callback_data="skip_tg")]
        ]),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "skip_tg")
async def cb_skip_tg(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(tg_channels=[])
    await state.set_state(GiveawayStates.channels_ig)
    await ask_ig(callback.message)
    await callback.answer()


@dp.message(GiveawayStates.channels_tg)
async def process_tg_channels(message: types.Message, state: FSMContext):
    chs = [c.strip() for c in message.text.replace(",", "\n").split("\n") if c.strip()]
    chs = [c if c.startswith("@") else "@" + c for c in chs]
    data = await state.get_data()
    existing = data.get("tg_channels", []) + chs
    await state.update_data(tg_channels=existing)
    await state.set_state(GiveawayStates.channels_ig)
    await message.answer(f"✅ Каналов добавлено: {len(existing)}")
    await ask_ig(message)


async def ask_ig(message):
    await message.answer(
        "📸 <b>Шаг 7/8 — Instagram</b>\n\nВведи @username или пропусти:",
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
    await state.update_data(ig_username="@" + message.text.strip().lstrip("@"))
    await state.set_state(GiveawayStates.winners_count)
    await ask_winners(message)


async def ask_winners(message):
    btns = [[InlineKeyboardButton(text=str(i), callback_data=f"wcount_{i}") for i in range(1, 6)]]
    await message.answer(
        "🏆 <b>Шаг 8/8 — Победители</b>\n\nСколько победителей?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("wcount_"))
async def cb_wcount(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(winners_count=int(callback.data.split("_")[1]))
    await state.set_state(GiveawayStates.end_time)
    await callback.message.answer(
        "⏰ Когда завершить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1 день", callback_data="et_1d"),
             InlineKeyboardButton(text="3 дня", callback_data="et_3d")],
            [InlineKeyboardButton(text="Неделя", callback_data="et_7d"),
             InlineKeyboardButton(text="Без ограничений", callback_data="et_none")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("et_"))
async def cb_et(callback: types.CallbackQuery, state: FSMContext):
    from datetime import timedelta
    val = callback.data[3:]
    now = datetime.now()
    end = {
        "1d": (now + timedelta(1)).strftime("%d.%m.%Y %H:%M"),
        "3d": (now + timedelta(3)).strftime("%d.%m.%Y %H:%M"),
        "7d": (now + timedelta(7)).strftime("%d.%m.%Y %H:%M"),
    }.get(val, "")
    await state.update_data(end_time=end)
    await finish_creation(callback.message, state, callback.from_user.id)
    await callback.answer()


@dp.message(GiveawayStates.end_time)
async def process_et(message: types.Message, state: FSMContext):
    await state.update_data(end_time=message.text.strip())
    await finish_creation(message, state, message.from_user.id)


async def finish_creation(message, state, user_id):
    data = await state.get_data()
    await state.clear()
    gid = db.create_giveaway(
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
    g = db.get_giveaway(gid)
    pt = giveaway_post_text(g, 0)
    kb = participate_kb(gid, g["button_label"])
    await message.answer("✅ <b>Розыгрыш создан! Предпросмотр:</b>", parse_mode="HTML")
    if g.get("photo_id"):
        await message.answer_photo(photo=g["photo_id"], caption=pt, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(pt, reply_markup=kb, parse_mode="HTML")
    await message.answer(
        "Управление:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Опубликовать в канал", callback_data=f"publish_{gid}")],
            [InlineKeyboardButton(text="⚙️ Управление", callback_data=f"manage_{gid}")]
        ])
    )


# ──────────────────────────────────────────────
# Мои розыгрыши
# ──────────────────────────────────────────────
@dp.message(Command("mygiveaways"))
async def cmd_mygiveaways(message: types.Message):
    if message.chat.type != "private":
        return
    gs = db.get_user_giveaways(message.from_user.id)
    if not gs:
        await message.answer("Нет розыгрышей. /new")
        return
    btns = [
        [InlineKeyboardButton(
            text=f"🟢 {g['title'][:30]} ({db.get_participant_count(g['id'])} уч.)",
            callback_data=f"manage_{g['id']}"
        )]
        for g in gs
    ]
    await message.answer("📋 Твои розыгрыши:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))


@dp.callback_query(F.data.startswith("manage_"))
async def cb_manage(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    pcount = db.get_participant_count(gid)
    circle = color_circle(g.get("button_color", "🔵 Синий"))
    await callback.message.answer(
        f"⚙️ <b>{g['title']}</b>\n\n"
        f"👥 Участников: {pcount}\n"
        f"🏆 Победителей: {g['winners_count']}\n"
        f"🎨 Цвет: {circle} {g.get('button_color', '')}\n"
        f"⏰ Итоги: {g.get('end_time') or 'не задано'}",
        reply_markup=admin_kb(gid),
        parse_mode="HTML"
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Публикация — выбор канала из тех, где бот admin
# ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("publish_"))
async def cb_publish(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    # Берём сохранённые каналы из розыгрыша + просим ввести вручную
    tg_chs = g.get("tg_channels", [])

    # Проверяем в каких каналах бот является администратором
    admin_channels = []
    for ch in tg_chs:
        try:
            me = await bot.get_chat_member(ch, (await bot.get_me()).id)
            if me.status in ("administrator", "creator"):
                chat = await bot.get_chat(ch)
                admin_channels.append((ch, chat.title or ch))
        except Exception:
            pass

    if admin_channels:
        btns = [
            [InlineKeyboardButton(text=f"📢 {title}", callback_data=f"pubchan_{gid}_{ch}")]
            for ch, title in admin_channels
        ]
        btns.append([InlineKeyboardButton(text="✏️ Ввести другой канал", callback_data=f"pubcustom_{gid}")])
        await callback.message.answer(
            "📢 <b>Выбери канал для публикации:</b>\n\n"
            "Показаны каналы из списка подписок, где бот является администратором.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            parse_mode="HTML"
        )
    else:
        await callback.message.answer(
            "📢 <b>Публикация в канал</b>\n\n"
            "Введи @username канала, где бот является администратором:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"manage_{gid}")]
            ]),
            parse_mode="HTML"
        )
        db.set_pending_publish(callback.from_user.id, gid)

    await callback.answer()


@dp.callback_query(F.data.startswith("pubchan_"))
async def cb_pubchan(callback: types.CallbackQuery):
    # pubchan_{gid}_{channel}
    parts = callback.data.split("_", 2)
    gid = parts[1]
    channel = parts[2]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await do_publish(callback.message, gid, g, channel)
    await callback.answer()


@dp.callback_query(F.data.startswith("pubcustom_"))
async def cb_pubcustom(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await callback.message.answer(
        "✏️ Введи @username канала для публикации:\n\nБот должен быть администратором канала.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"manage_{gid}")]
        ])
    )
    db.set_pending_publish(callback.from_user.id, gid)
    await callback.answer()


@dp.message(F.text.startswith("@"), F.chat.type == "private")
async def handle_channel_input(message: types.Message):
    """Ловим ввод @канала для публикации."""
    pending = db.get_pending_publish(message.from_user.id)
    if not pending:
        return
    gid = pending
    db.clear_pending_publish(message.from_user.id)
    g = db.get_giveaway(gid)
    if not g:
        await message.answer("❌ Розыгрыш не найден.")
        return
    channel = message.text.strip()
    await do_publish(message, gid, g, channel)


async def do_publish(message, gid, g, channel):
    """Публикует пост розыгрыша в указанный канал."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel, me.id)
        if member.status not in ("administrator", "creator"):
            await message.answer(f"❌ Бот не является администратором канала {channel}.")
            return
    except Exception as e:
        await message.answer(f"❌ Не удалось проверить права в канале {channel}.\nОшибка: {e}")
        return

    pcount = db.get_participant_count(gid)
    pt = giveaway_post_text(g, pcount)
    kb = participate_kb(gid, g["button_label"])

    try:
        if g.get("photo_id"):
            msg = await bot.send_photo(
                chat_id=channel,
                photo=g["photo_id"],
                caption=pt,
                reply_markup=kb,
                parse_mode="HTML"
            )
        else:
            msg = await bot.send_message(
                chat_id=channel,
                text=pt,
                reply_markup=kb,
                parse_mode="HTML"
            )
        db.update_giveaway_message(gid, channel, msg.message_id)
        await message.answer(
            f"✅ <b>Опубликовано в {channel}!</b>\n\n"
            f"Кнопка участия работает везде — в канале, при пересылке поста.\n"
            f"При нажатии бот откроет личку и проведёт проверку.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Управление розыгрышем", callback_data=f"manage_{gid}")]
            ])
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при публикации: {e}")


# ──────────────────────────────────────────────
# Капча — inline кнопки прямо в боте
# ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("captcha_"))
async def cb_captcha(callback: types.CallbackQuery):
    # формат: captcha_{gid}_{answer}_{chosen}
    parts = callback.data.split("_", 3)
    gid = parts[1]
    answer = parts[2]
    chosen = parts[3]

    user = callback.from_user
    g = db.get_giveaway(gid)
    if not g or g["status"] != "active":
        await callback.answer("❌ Розыгрыш завершён.", show_alert=True)
        return
    if db.is_participant(gid, user.id):
        await callback.answer("✅ Ты уже участвуешь!", show_alert=True)
        try:
            await callback.message.delete()
        except:
            pass
        return

    if chosen != answer:
        db.set_captcha(user.id, gid, answer)
        await callback.answer("❌ Неверно, попробуй ещё раз!")
        try:
            await callback.message.edit_reply_markup(
                reply_markup=captcha_inline_kb(gid, answer)
            )
        except:
            pass
        return

    # Верно
    db.clear_captcha(user.id, gid)
    tg_channels = g.get("tg_channels", [])
    not_subbed = [ch for ch in tg_channels if not await check_tg_sub(user.id, ch)]

    try:
        await callback.message.delete()
    except:
        pass

    if not_subbed:
        btns = [
            [InlineKeyboardButton(text=f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}")]
            for ch in not_subbed
        ]
        btns.append([InlineKeyboardButton(
            text="✅ Я подписался — проверить",
            callback_data=f"checksub_{gid}"
        )])
        await callback.message.answer(
            "⚠️ <b>Подпишись на каналы:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=btns),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    await callback.answer("✅ Проверка пройдена!")
    await do_register(callback.message, user, gid, g)


@dp.callback_query(F.data.startswith("checksub_"))
async def cb_checksub(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    user = callback.from_user
    if db.is_participant(gid, user.id):
        await callback.answer("✅ Уже участвуешь!", show_alert=True)
        return
    not_subbed = [ch for ch in g.get("tg_channels", []) if not await check_tg_sub(user.id, ch)]
    if not_subbed:
        await callback.answer("❌ Ещё не подписан на все каналы!", show_alert=True)
        return
    await do_register(callback.message, user, gid, g)
    await callback.answer()


async def do_register(message, user, gid, g):
    uname = user.username
    display = f"@{uname}" if uname else user.first_name
    link = f"https://t.me/{uname}" if uname else f"tg://user?id={user.id}"
    db.add_participant(gid, user.id, display, link)
    pcount = db.get_participant_count(gid)
    ig = g.get("ig_username", "")
    ig_line = f"\n📸 Подпишись в Instagram: {ig}" if ig else ""
    extra = [
        [InlineKeyboardButton(text="📸 Instagram", url=f"https://instagram.com/{ig.lstrip('@')}")]
    ] if ig else []
    await message.answer(
        f"🎉 <b>Ты в розыгрыше!</b>\n\n<b>{g['title']}</b>\n\n"
        f"👤 Участник #{pcount}{ig_line}\n\n"
        f"🍀 Удачи! Итоги: {g.get('end_time') or 'по решению организатора'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=extra) if extra else None,
        parse_mode="HTML"
    )
    # Обновляем счётчик в опубликованном посте
    try:
        pi = db.get_giveaway_post(gid)
        if pi:
            pt = giveaway_post_text(g, pcount)
            kb = participate_kb(gid, g["button_label"])
            if g.get("photo_id"):
                await bot.edit_message_caption(
                    chat_id=pi["chat_id"],
                    message_id=pi["message_id"],
                    caption=pt,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            else:
                await bot.edit_message_text(
                    chat_id=pi["chat_id"],
                    message_id=pi["message_id"],
                    text=pt,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
    except TelegramBadRequest:
        pass


# ──────────────────────────────────────────────
# Список участников
# ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("adminlist_"))
async def cb_adminlist(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Список доступен только создателю.", show_alert=True)
        return
    participants = db.get_participants(gid)
    secret_id = db.get_secret_winner(gid)
    if not participants:
        await callback.answer("Участников пока нет.", show_alert=True)
        return
    lines = []
    for i, p in enumerate(participants):
        mark = " 🎯" if p["user_id"] == secret_id else ""
        link = p.get("profile_link", "")
        name = p["username"]
        lines.append(f"{i+1}. <a href='{link}'>{name}</a>{mark}" if link else f"{i+1}. {name}{mark}")
    for idx, chunk in enumerate([lines[i:i+50] for i in range(0, len(lines), 50)]):
        part = f" (часть {idx+1})" if len(lines) > 50 else ""
        text = f"👥 <b>Участники ({len(participants)}){part}:</b>\n" + "\n".join(chunk)
        if idx == len(lines)//50 and secret_id:
            text += "\n\n🎯 — назначен скрытно"
        await callback.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


# ──────────────────────────────────────────────
# Назначить победителя скрытно
# ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("setwinner_"))
async def cb_setwinner(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    ps = db.get_participants(gid)
    if not ps:
        await callback.answer("Нет участников!", show_alert=True)
        return
    btns = [
        [InlineKeyboardButton(text=p["username"], callback_data=f"confwinner_{gid}_{p['user_id']}")]
        for p in ps[:50]
    ]
    btns.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"manage_{gid}")])
    await callback.message.answer(
        "🎯 Выбери победителя (скрытно):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=btns)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("confwinner_"))
async def cb_confwinner(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    gid, wid = parts[1], int(parts[2])
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    db.set_secret_winner(gid, wid)
    w = db.get_participant_by_id(gid, wid)
    await callback.message.answer(
        f"✅ Назначен скрытно: <b>{w['username'] if w else wid}</b>\n\n"
        f"Нажми «Завершить» — выглядит как честный рандом.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Завершить", callback_data=f"draw_{gid}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Жеребьёвка
# ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("draw_"))
async def cb_draw(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    if g["status"] != "active":
        await callback.answer("Уже завершён.", show_alert=True)
        return
    ps = db.get_participants(gid)
    if not ps:
        await callback.answer("Нет участников!", show_alert=True)
        return
    sid = db.get_secret_winner(gid)
    wcount = min(g["winners_count"], len(ps))
    if sid:
        sec = next((p for p in ps if p["user_id"] == sid), None)
        others = [p for p in ps if p["user_id"] != sid]
        random.shuffle(others)
        winners = ([sec] + others[:wcount - 1]) if sec else others[:wcount]
    else:
        pool = ps[:]
        random.shuffle(pool)
        winners = pool[:wcount]
    db.finish_giveaway(gid, [w["user_id"] for w in winners])
    wlines = []
    for w in winners:
        link = w.get("profile_link", "")
        wlines.append(f"🥇 <a href='{link}'>{w['username']}</a>" if link else f"🥇 {w['username']}")
    result = (
        f"🎉 <b>Розыгрыш завершён!</b>\n\n<b>{g['title']}</b>\n\n"
        f"🏆 {'Победитель' if len(winners) == 1 else 'Победители'}:\n"
        + "\n".join(wlines)
        + f"\n\n👥 Участвовало: {len(ps)}\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    if g.get("photo_id"):
        await callback.message.answer_photo(photo=g["photo_id"], caption=result, parse_mode="HTML")
    else:
        await callback.message.answer(result, parse_mode="HTML", disable_web_page_preview=True)
    pi = db.get_giveaway_post(gid)
    if pi:
        try:
            fin = result + "\n\n✅ <i>Розыгрыш завершён</i>"
            if g.get("photo_id"):
                await bot.edit_message_caption(
                    chat_id=pi["chat_id"], message_id=pi["message_id"],
                    caption=fin, parse_mode="HTML"
                )
            else:
                await bot.edit_message_text(
                    chat_id=pi["chat_id"], message_id=pi["message_id"],
                    text=fin, parse_mode="HTML", disable_web_page_preview=True
                )
        except Exception:
            pass
    await callback.answer("✅ Завершено!")


# ──────────────────────────────────────────────
# Отмена
# ──────────────────────────────────────────────
@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    await callback.message.answer(
        "⚠️ Отменить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data=f"cancelok_{gid}"),
             InlineKeyboardButton(text="↩️ Нет", callback_data=f"manage_{gid}")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancelok_"))
async def cb_cancelok(callback: types.CallbackQuery):
    gid = callback.data.split("_", 1)[1]
    g = db.get_giveaway(gid)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    db.cancel_giveaway(gid)
    await callback.message.edit_text("❌ Розыгрыш отменён.")
    await callback.answer()


# ──────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────
async def main():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username
    logger.info(f"Бот запущен: @{BOT_USERNAME}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
