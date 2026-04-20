import asyncio
import logging
import random
import json
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberAdministrator, ChatMemberOwner
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

from config import BOT_TOKEN, ADMIN_IDS
from database import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class GiveawayStates(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_winners_count = State()
    waiting_secret_winner = State()


# ─── Вспомогательные функции ───────────────────────────────────────────────

async def is_admin(user_id: int, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))
    except Exception:
        return False


async def is_bot_admin(chat_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))
    except Exception:
        return False


def giveaway_keyboard(giveaway_id: str, participant_count: int = 0):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🎁 Участвовать ({participant_count})",
            callback_data=f"join_{giveaway_id}"
        )],
        [InlineKeyboardButton(
            text="👥 Список участников",
            callback_data=f"list_{giveaway_id}"
        )]
    ])


def admin_giveaway_keyboard(giveaway_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Завершить и выбрать победителя", callback_data=f"draw_{giveaway_id}")],
        [InlineKeyboardButton(text="🎯 Назначить победителя (скрытно)", callback_data=f"setwinner_{giveaway_id}")],
        [InlineKeyboardButton(text="📋 Участники", callback_data=f"adminlist_{giveaway_id}")],
        [InlineKeyboardButton(text="❌ Отменить розыгрыш", callback_data=f"cancel_{giveaway_id}")]
    ])


# ─── /start ────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    text = (
        "👋 <b>Привет! Я бот для розыгрышей.</b>\n\n"
        "Добавь меня в свой канал или группу как администратора — "
        "и можно проводить розыгрыши!\n\n"
        "<b>Команды для создания розыгрыша:</b>\n"
        "• /new — создать новый розыгрыш\n"
        "• /mygiveaways — мои активные розыгрыши\n\n"
        "<b>Команды администратора (в личке):</b>\n"
        "• /setwinner — назначить нужного победителя\n"
        "• /draw — завершить и объявить победителя\n"
        "• /cancel — отменить розыгрыш\n\n"
        "ℹ️ Напиши /help для подробной инструкции."
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📖 <b>Инструкция по использованию</b>\n\n"
        "<b>1. Добавь бота в канал/группу</b>\n"
        "Выдай права администратора с возможностью публиковать сообщения.\n\n"
        "<b>2. Создай розыгрыш</b>\n"
        "Напиши /new в личку боту — он попросит название, описание и количество победителей.\n\n"
        "<b>3. Опубликуй в канале</b>\n"
        "Бот пришлёт кнопку «Опубликовать» — нажми и выбери нужный канал.\n\n"
        "<b>4. Участники нажимают «Участвовать»</b>\n"
        "Бот автоматически регистрирует их.\n\n"
        "<b>5. Завершение</b>\n"
        "Используй /mygiveaways → «Завершить» — победитель выбирается случайно.\n"
        "Или /setwinner — если хочешь выбрать конкретного человека (скрытно).\n\n"
        "⚡ Результат всегда выглядит как честный рандом для участников."
    )
    await message.answer(text, parse_mode="HTML")


# ─── Создание розыгрыша ────────────────────────────────────────────────────

@dp.message(Command("new"))
async def cmd_new(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await message.reply("⚠️ Создавать розыгрыши можно только в личных сообщениях боту.")
        return
    await state.set_state(GiveawayStates.waiting_title)
    await message.answer(
        "🎁 <b>Создание нового розыгрыша</b>\n\n"
        "Шаг 1/3: Введи <b>название</b> розыгрыша:",
        parse_mode="HTML"
    )


@dp.message(GiveawayStates.waiting_title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayStates.waiting_description)
    await message.answer(
        "Шаг 2/3: Введи <b>описание</b> розыгрыша\n"
        "(условия участия, приз и т.д.):",
        parse_mode="HTML"
    )


@dp.message(GiveawayStates.waiting_description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(GiveawayStates.waiting_winners_count)
    await message.answer(
        "Шаг 3/3: Сколько победителей? (введи число, например <code>1</code>):",
        parse_mode="HTML"
    )


@dp.message(GiveawayStates.waiting_winners_count)
async def process_winners_count(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) < 1:
        await message.answer("⚠️ Введи корректное число (от 1).")
        return

    count = int(message.text)
    data = await state.get_data()
    await state.clear()

    giveaway_id = db.create_giveaway(
        creator_id=message.from_user.id,
        title=data["title"],
        description=data["description"],
        winners_count=count
    )

    preview_text = (
        f"🎁 <b>{data['title']}</b>\n\n"
        f"{data['description']}\n\n"
        f"🏆 Победителей: <b>{count}</b>\n"
        f"👥 Участников: <b>0</b>\n"
        f"📅 Создан: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Опубликовать в канале/группе", callback_data=f"publish_{giveaway_id}")],
        [InlineKeyboardButton(text="⚙️ Управление", callback_data=f"manage_{giveaway_id}")]
    ])

    await message.answer(
        f"✅ Розыгрыш создан!\n\n<b>Предпросмотр:</b>\n\n{preview_text}",
        reply_markup=kb,
        parse_mode="HTML"
    )


# ─── Мои розыгрыши ─────────────────────────────────────────────────────────

@dp.message(Command("mygiveaways"))
async def cmd_mygiveaways(message: types.Message):
    if message.chat.type != "private":
        return
    giveaways = db.get_user_giveaways(message.from_user.id)
    if not giveaways:
        await message.answer("У тебя пока нет активных розыгрышей. Создай новый: /new")
        return

    buttons = []
    for g in giveaways:
        pcount = db.get_participant_count(g["id"])
        status = "🟢" if g["status"] == "active" else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {g['title']} ({pcount} уч.)",
            callback_data=f"manage_{g['id']}"
        )])

    await message.answer(
        "📋 <b>Твои розыгрыши:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


# ─── Callback: управление ──────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("manage_"))
async def cb_manage(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    pcount = db.get_participant_count(giveaway_id)
    text = (
        f"⚙️ <b>Управление розыгрышем</b>\n\n"
        f"<b>{g['title']}</b>\n"
        f"{g['description']}\n\n"
        f"🏆 Победителей: {g['winners_count']}\n"
        f"👥 Участников: {pcount}\n"
        f"📌 Статус: {'🟢 Активен' if g['status'] == 'active' else '🔴 Завершён'}"
    )
    await callback.message.edit_text(text, reply_markup=admin_giveaway_keyboard(giveaway_id), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("publish_"))
async def cb_publish(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    await callback.message.edit_text(
        "📢 Перешли это сообщение в нужный канал или группу,\n"
        "либо нажми кнопку ниже (если бот уже добавлен как админ).\n\n"
        "Или введи @username канала/группы:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data=f"manage_{giveaway_id}")]
        ])
    )

    post_text = build_post_text(g, 0)
    kb = giveaway_keyboard(giveaway_id, 0)
    msg = await callback.message.answer(post_text, reply_markup=kb, parse_mode="HTML")
    db.update_giveaway_message(giveaway_id, callback.message.chat.id, msg.message_id)
    await callback.answer()


def build_post_text(g: dict, participant_count: int) -> str:
    return (
        f"🎁 <b>{g['title']}</b>\n\n"
        f"{g['description']}\n\n"
        f"🏆 Победителей: <b>{g['winners_count']}</b>\n"
        f"👥 Участников: <b>{participant_count}</b>\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"⬇️ Нажми кнопку, чтобы участвовать!"
    )


# ─── Callback: участие ─────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("join_"))
async def cb_join(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)

    if not g or g["status"] != "active":
        await callback.answer("❌ Розыгрыш уже завершён.", show_alert=True)
        return

    user = callback.from_user
    already = db.add_participant(giveaway_id, user.id, user.username or user.first_name)

    if already:
        await callback.answer("✅ Ты уже участвуешь в этом розыгрыше!", show_alert=True)
        return

    pcount = db.get_participant_count(giveaway_id)
    await callback.answer(f"🎉 Ты зарегистрирован! Участников: {pcount}", show_alert=True)

    # Обновляем кнопку с новым счётчиком
    try:
        await callback.message.edit_reply_markup(
            reply_markup=giveaway_keyboard(giveaway_id, pcount)
        )
    except TelegramBadRequest:
        pass


@dp.callback_query(F.data.startswith("list_"))
async def cb_list(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    participants = db.get_participants(giveaway_id)

    if not participants:
        await callback.answer("Участников пока нет.", show_alert=True)
        return

    lines = [f"{i+1}. @{p['username']}" if p["username"].startswith("@") else f"{i+1}. {p['username']}"
             for i, p in enumerate(participants[:50])]
    text = "👥 <b>Участники:</b>\n" + "\n".join(lines)
    if len(participants) > 50:
        text += f"\n...и ещё {len(participants) - 50}"
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")


# ─── Callback: назначить победителя (скрытно) ──────────────────────────────

@dp.callback_query(F.data.startswith("setwinner_"))
async def cb_setwinner(callback: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    participants = db.get_participants(giveaway_id)
    if not participants:
        await callback.answer("Нет участников!", show_alert=True)
        return

    buttons = []
    for p in participants:
        name = p["username"] if p["username"].startswith("@") else p["username"]
        buttons.append([InlineKeyboardButton(
            text=name,
            callback_data=f"confirmwinner_{giveaway_id}_{p['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"manage_{giveaway_id}")])

    await callback.message.edit_text(
        "🎯 <b>Выбери нужного победителя</b>\n"
        "(никто кроме тебя не узнает об этом выборе):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("confirmwinner_"))
async def cb_confirmwinner(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    giveaway_id = parts[1]
    winner_user_id = int(parts[2])

    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    db.set_secret_winner(giveaway_id, winner_user_id)
    winner = db.get_participant_by_id(giveaway_id, winner_user_id)
    name = winner["username"] if winner else str(winner_user_id)

    await callback.message.edit_text(
        f"✅ <b>Победитель назначен скрытно:</b> {name}\n\n"
        f"Теперь нажми «Завершить» — результат выглядит как честный рандом.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Завершить розыгрыш", callback_data=f"draw_{giveaway_id}")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data=f"manage_{giveaway_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


# ─── Callback: розыгрыш / жеребьёвка ──────────────────────────────────────

@dp.callback_query(F.data.startswith("draw_"))
async def cb_draw(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)

    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    if g["status"] != "active":
        await callback.answer("Розыгрыш уже завершён.", show_alert=True)
        return

    participants = db.get_participants(giveaway_id)
    if len(participants) < 1:
        await callback.answer("❌ Нет участников!", show_alert=True)
        return

    secret_winner_id = db.get_secret_winner(giveaway_id)
    winners_count = min(g["winners_count"], len(participants))

    if secret_winner_id:
        secret = next((p for p in participants if p["user_id"] == secret_winner_id), None)
        others = [p for p in participants if p["user_id"] != secret_winner_id]
        random.shuffle(others)
        winners = [secret] + others[:winners_count - 1]
    else:
        shuffled = participants[:]
        random.shuffle(shuffled)
        winners = shuffled[:winners_count]

    db.finish_giveaway(giveaway_id, [w["user_id"] for w in winners])

    winner_names = "\n".join(
        f"🥇 @{w['username']}" if not w["username"].startswith("@") else f"🥇 {w['username']}"
        for w in winners
    )

    result_text = (
        f"🎉 <b>Розыгрыш завершён!</b>\n\n"
        f"<b>{g['title']}</b>\n\n"
        f"🏆 <b>{'Победитель' if len(winners) == 1 else 'Победители'}:</b>\n"
        f"{winner_names}\n\n"
        f"👥 Всего участвовало: {len(participants)}\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    await callback.message.answer(result_text, parse_mode="HTML")

    # Обновляем исходный пост если он был
    post_info = db.get_giveaway_post(giveaway_id)
    if post_info:
        try:
            await bot.edit_message_text(
                chat_id=post_info["chat_id"],
                message_id=post_info["message_id"],
                text=result_text + "\n\n✅ <i>Розыгрыш завершён</i>",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer("✅ Розыгрыш завершён!")


# ─── Callback: отменить ────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"cancelconfirm_{giveaway_id}")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data=f"manage_{giveaway_id}")]
    ])
    await callback.message.edit_text(
        "⚠️ Ты уверен, что хочешь отменить этот розыгрыш?",
        reply_markup=kb
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancelconfirm_"))
async def cb_cancelconfirm(callback: types.CallbackQuery):
    giveaway_id = callback.data.split("_", 1)[1]
    g = db.get_giveaway(giveaway_id)
    if not g or g["creator_id"] != callback.from_user.id:
        await callback.answer("❌ Нет доступа.", show_alert=True)
        return
    db.cancel_giveaway(giveaway_id)
    await callback.message.edit_text("❌ Розыгрыш отменён.")
    await callback.answer()


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
        marker = " 🎯" if p["user_id"] == secret_id else ""
        name = p["username"] if p["username"].startswith("@") else f"@{p['username']}"
        lines.append(f"{i+1}. {name}{marker}")

    text = f"👥 <b>Участники ({len(participants)}):</b>\n" + "\n".join(lines[:100])
    if secret_id:
        text += "\n\n🎯 — скрытно назначен победителем"
    await callback.answer()
    await callback.message.answer(text, parse_mode="HTML")


# ─── Запуск ────────────────────────────────────────────────────────────────

async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
