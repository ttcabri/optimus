from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import database

router = Router()


class Onboarding(StatesGroup):
    waiting_name = State()
    waiting_goal = State()
    waiting_home_time = State()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = await database.get_user(message.from_user.id)
    if user is not None:
        await message.answer(f"Уже знакомы, {user['name']}. Главная цель: {user['main_goal']}.")
        return

    await state.set_state(Onboarding.waiting_name)
    await message.answer("Привет. Я твой ментор. Не друг — ментор. Как тебя зовут?")


@router.message(Onboarding.waiting_name)
async def onboarding_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(Onboarding.waiting_goal)
    await message.answer("Одна главная цель на 90 дней. Не список — одна. Что это?")


@router.message(Onboarding.waiting_goal)
async def onboarding_goal(message: Message, state: FSMContext) -> None:
    await state.update_data(main_goal=message.text)
    await state.set_state(Onboarding.waiting_home_time)
    await message.answer("Во сколько ты обычно приходишь домой с работы?")


@router.message(Onboarding.waiting_home_time)
async def onboarding_home_time(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await database.create_user(
        telegram_id=message.from_user.id,
        name=data["name"],
        main_goal=data["main_goal"],
    )
    await state.clear()
    await message.answer(
        "Принято. Каждый день в 16:30 я буду спрашивать что ты сделал. Без исключений."
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    user = await database.get_user(message.from_user.id)
    if user is None:
        await message.answer("Сначала /start.")
        return

    streak = await database.get_streak(message.from_user.id)
    week_logs = await database.get_week_logs(message.from_user.id)
    days_with_action = sum(1 for log in week_logs if log["what_done"])

    await message.answer(
        f"Главная цель: {user['main_goal']}\n"
        f"Streak: {streak} дней подряд\n"
        f"За эту неделю: {days_with_action}/7 дней с действием"
    )


@router.message(Command("week"))
async def cmd_week(message: Message) -> None:
    user = await database.get_user(message.from_user.id)
    if user is None:
        await message.answer("Сначала /start.")
        return

    week_logs = await database.get_week_logs(message.from_user.id)
    if not week_logs:
        await message.answer("За эту неделю пока ничего не записано.")
        return

    lines = ["Сводка недели:"]
    for log in week_logs:
        done = log["what_done"] or "—"
        blocked = log["what_blocked"] or "—"
        lines.append(f"{log['date']}: сделал — {done}; помешало — {blocked}")

    await message.answer("\n".join(lines))


@router.message(Command("ideas"))
async def cmd_ideas(message: Message) -> None:
    ideas = await database.get_ideas(message.from_user.id)
    if not ideas:
        await message.answer("Список 'Потом' пуст.")
        return

    lines = ["Идеи в паркинге:"]
    for idea in ideas:
        lines.append(f"- {idea['idea']}")

    await message.answer("\n".join(lines))


@router.message(Command("goal"))
async def cmd_goal(message: Message, command: CommandObject) -> None:
    user = await database.get_user(message.from_user.id)
    if user is None:
        await message.answer("Сначала /start.")
        return

    if command.args:
        await database.update_main_goal(message.from_user.id, command.args)
        await message.answer(f"Цель обновлена: {command.args}")
        return

    await message.answer(f"Главная цель: {user['main_goal']}")
