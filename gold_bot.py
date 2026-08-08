"""Золотой бот (aiogram v3): курс ЦБ/Сбербанк в закрепе группы, новости про драгметаллы,
журнал сделок в Google Sheets по кнопкам.

Маркер версии: GOLD-BOT v1
Модули: rates.py (курсы), news.py (RSS-новости), journal.py (журнал сделок).

Режимы:
  🟡 Курс сейчас         — показать текущий курс (ЦБ доллар/999 + Сбер) в личке
  🟢 Купить / 🔴 Продать — внести сделку: вес -> цена/г -> запись в журнал + средние
  🏦 Курс Сбера (ручной) — запасной ввод курса Сбера, если автоподтяжка с зеркала не удалась
  📌 Обновить закреп      — пересобрать закреплённое сообщение в группе вручную
  📰 Новости сейчас       — разово запостить свежие новости в группу
Автоматика: закреп обновляется сам каждые config.PIN_REFRESH_MINUTES (дефолт 30 мин) во всех
чатах, где он закреплён; новости каждые config.NEWS_INTERVAL_HOURS.
"""
import asyncio
import datetime as dt
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

import config
import journal
import news
import rates

logging.basicConfig(level=logging.INFO)
dp = Dispatcher(storage=MemoryStorage())

STATE_DIR = Path(__file__).resolve().parent / "state"
PIN_FILE = STATE_DIR / "pin.json"
SBER_FILE = STATE_DIR / "sber_manual.json"
NEWS_SEEN = str(STATE_DIR / "news_seen.json")

PROFIT_RUB = int(getattr(config, "PROFIT_PER_GRAM", 500))   # надбавка к закупу для цены продажи, ₽/г

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟡 Курс сейчас"), KeyboardButton(text="📈 Анализ рынка")],
        [KeyboardButton(text="🟢 Купить"), KeyboardButton(text="🔴 Продать")],
        [KeyboardButton(text="🧮 Разница с Сбером"), KeyboardButton(text="📌 Обновить закреп")],
        [KeyboardButton(text="📰 Новости сейчас")],
    ],
    resize_keyboard=True,
)

CANCEL_WORDS = {"отмена", "/cancel", "стоп", "/stop"}


class DealForm(StatesGroup):
    waiting_weight = State()
    waiting_price = State()


class SberForm(StatesGroup):
    waiting_values = State()


class CalcForm(StatesGroup):
    waiting_price = State()


# ---------------- утилиты ----------------
def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_json(path, data):
    STATE_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def is_admin(uid):
    ids = getattr(config, "ADMIN_IDS", []) or []
    return not ids or uid in ids


def parse_num(text):
    if not text:
        return None
    s = text.replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def _safe_cbr_gold():
    try:
        g = rates.fetch_cbr_gold()
        return g["price999"] if g else ""
    except Exception:
        return ""


# ---------------- закреп ----------------
def _load_pins():
    """Словарь закрепов {str(chat_id): message_id}. Мигрирует старый формат {chat_id,message_id}."""
    data = _load_json(PIN_FILE) or {}
    if "message_id" in data and "chat_id" in data:      # старый одиночный формат
        return {str(data["chat_id"]): data["message_id"]}
    return {str(k): v for k, v in data.items()}


async def update_pin(bot, chat_id):
    """Обновляет (или создаёт+закрепляет) курс в чате chat_id. Помнит закреп по каждому чату,
    поэтому закреп может жить сразу в личке и в группе. Возвращает message_id или None."""
    if not chat_id:
        return None
    manual = _load_json(SBER_FILE)
    r = await asyncio.to_thread(rates.build_rates, manual)
    text = rates.render_pin(r)

    pins = _load_pins()
    mid = pins.get(str(chat_id))
    if mid:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=mid)
            return mid
        except Exception as e:
            if "not modified" in str(e).lower():
                return mid
            # сообщение удалено/недоступно — отправим новое
    msg = await bot.send_message(chat_id, text)
    try:
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
    except Exception as e:
        logging.warning("не удалось закрепить (нет прав в группе?): %s", e)
    pins[str(chat_id)] = msg.message_id
    _save_json(PIN_FILE, pins)
    return msg.message_id


# ---------------- новости ----------------
async def news_cycle(bot):
    gid = getattr(config, "GROUP_CHAT_ID", 0)
    if not gid:
        return 0
    items = await asyncio.to_thread(
        news.select_new, config.NEWS_RSS, config.NEWS_KEYWORDS,
        NEWS_SEEN, config.NEWS_MAX_PER_CYCLE)
    for it in items:
        try:
            await bot.send_message(gid, news.format_news(it, config.NEWS_CTA))
        except Exception as e:
            logging.warning("не отправил новость: %s", e)
        await asyncio.sleep(1)
    return len(items)


# ---------------- планировщики ----------------
async def pin_loop(bot):
    """Сам обновляет закреп во всех чатах, где он стоит: сразу после запуска и далее каждые
    PIN_REFRESH_MINUTES (дефолт 30). Периодический опрос надёжнее «раз в сутки в такой-то час»:
    перезапуск бота больше не пропускает окно обновления. Если текст не изменился — Telegram
    вернёт «not modified», update_pin это проглотит, лишних сообщений не будет."""
    await asyncio.sleep(15)   # дать боту подняться
    every = max(5, int(getattr(config, "PIN_REFRESH_MINUTES", 30))) * 60
    while True:
        try:
            targets = [int(c) for c in _load_pins().keys()]
            if not targets and getattr(config, "GROUP_CHAT_ID", 0):
                targets = [config.GROUP_CHAT_ID]
            for chat_id in targets:
                await update_pin(bot, chat_id)
        except Exception as e:
            logging.exception("pin_loop: %s", e)
        await asyncio.sleep(every)


async def news_loop(bot):
    await asyncio.sleep(20)  # дать боту подняться
    while True:
        try:
            await news_cycle(bot)
        except Exception as e:
            logging.exception("news_loop: %s", e)
        await asyncio.sleep(getattr(config, "NEWS_INTERVAL_HOURS", 4) * 3600)


# ---------------- хендлеры ----------------
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🟡 Золотой бот на связи. Выбери действие:", reply_markup=MAIN_KB)


@dp.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"chat_id: <code>{m.chat.id}</code>\ntype: {m.chat.type}\n"
                   f"твой id: <code>{m.from_user.id}</code>")


@dp.message(Command("zakrep"))
async def cmd_zakrep(m: Message):
    """Закрепить курс в текущем чате (работает и в группе: сделай бота админом с правом закрепа)."""
    if not is_admin(m.from_user.id):
        return await m.answer("Только для владельца.")
    mid = await update_pin(m.bot, m.chat.id)
    await m.answer("📌 Курс закреплён в этом чате, буду обновлять его каждый день." if mid
                   else "Не вышло закрепить — проверь, что бот админ с правом закреплять сообщения.")


@dp.message(F.text == "🟡 Курс сейчас")
async def show_rates(m: Message):
    await m.answer("Считаю курс…")
    manual = _load_json(SBER_FILE)
    r = await asyncio.to_thread(rates.build_rates, manual)
    await m.answer(rates.render_pin(r))


@dp.message(F.text == "📈 Анализ рынка")
async def market(m: Message):
    await m.answer("Анализирую рынок золота и серебра…")
    try:
        text = await asyncio.to_thread(rates.render_market)
    except Exception as e:
        return await m.answer(f"Не удалось получить данные ЦБ: {e}")
    await m.answer(text)


# --- калькулятор: разница с Сбером по введённой цене (без записи в журнал) ---
@dp.message(F.text == "🧮 Разница с Сбером")
async def calc_start(m: Message, state: FSMContext):
    await state.set_state(CalcForm.waiting_price)
    await m.answer("🧮 Введи цену за грамм (₽/г), по которой хочешь купить — "
                   "посчитаю разницу с курсом Сбербанка 585. Или «отмена».")


@dp.message(CalcForm.waiting_price)
async def calc_price(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() in CANCEL_WORDS:
        await state.clear()
        return await m.answer("Отменил.", reply_markup=MAIN_KB)
    p = parse_num(m.text)
    if p is None:
        return await m.answer("Не понял цену. Введи число ₽/г, напр. 3900")
    await state.clear()
    manual = _load_json(SBER_FILE)
    r = await asyncio.to_thread(rates.build_rates, manual)
    sell585 = r.get("sber_sell585")
    if not sell585:
        return await m.answer("Курс Сбербанка сейчас недоступен. Введи его вручную кнопкой "
                              "«🏦 Курс Сбера (ручной)» и повтори.", reply_markup=MAIN_KB)
    d = sell585 - p   # продажа Сбера (слиток) − твоя цена лома
    if d > 0:
        verdict = f"лом дешевле слитка Сбера на <b>{d:.0f} ₽/г</b> — выгоднее брать лом ✅"
    elif d < 0:
        verdict = f"лом дороже слитка Сбера на <b>{abs(d):.0f} ₽/г</b> — выгоднее купить слиток у Сбера"
    else:
        verdict = "цена равна продаже Сбера"
    await m.answer(
        f"🧮 Твоя цена (лом): <b>{p:.0f} ₽/г</b>\n"
        f"🏦 Сбербанк 585 продажа (слиток): <b>{sell585:.0f} ₽/г</b>\n\n"
        f"{verdict}",
        reply_markup=MAIN_KB)


@dp.message(F.text == "📌 Обновить закреп")
async def force_pin(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("Только для владельца.")
    mid = await update_pin(m.bot, m.chat.id)   # закрепляем прямо здесь, в личке
    await m.answer("📌 Курс обновлён и закреплён в этом чате." if mid
                   else "Не вышло обновить закреп.")


@dp.message(F.text == "📰 Новости сейчас")
async def force_news(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("Только для владельца.")
    if not getattr(config, "GROUP_CHAT_ID", 0):
        return await m.answer("Не задан GROUP_CHAT_ID в config.py.")
    n = await news_cycle(m.bot)
    await m.answer(f"📰 Отправлено новостей: {n}" if n else "Новых новостей по драгметаллам нет.")


# --- ручной курс Сбера ---
@dp.message(F.text == "🏦 Курс Сбера (ручной)")
async def sber_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return await m.answer("Только для владельца.")
    await state.set_state(SberForm.waiting_values)
    await m.answer("Введи курс Сбера 999 двумя числами через пробел: <b>покупка продажа</b>\n"
                   "Напр.: <code>10109 10743</code>\n(или «отмена»)")


@dp.message(SberForm.waiting_values)
async def sber_values(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() in CANCEL_WORDS:
        await state.clear()
        return await m.answer("Отменил.", reply_markup=MAIN_KB)
    parts = (m.text or "").replace(",", ".").split()
    nums = [parse_num(p) for p in parts]
    nums = [n for n in nums if n]
    if len(nums) < 2:
        return await m.answer("Нужно два числа: покупка продажа. Напр.: 10109 10743")
    buy, sell = min(nums[0], nums[1]), max(nums[0], nums[1])
    _save_json(SBER_FILE, {"buy999": buy, "sell999": sell})
    await state.clear()
    await m.answer(f"✅ Курс Сбера сохранён (ручной): покупка {buy:.0f} / продажа {sell:.0f} ₽/г.\n"
                   f"Будет использован в закрепе, если автоподтяжка не сработает.",
                   reply_markup=MAIN_KB)


# --- сделка: Купить / Продать ---
@dp.message(F.text == "🟢 Купить")
async def buy_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return await m.answer("Только для владельца.")
    await state.set_state(DealForm.waiting_weight)
    await state.update_data(kind="Покупка")
    await m.answer("🟢 <b>Покупка.</b> Введи вес в граммах (напр. 12.5). Или «отмена».")


@dp.message(F.text == "🔴 Продать")
async def sell_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return await m.answer("Только для владельца.")
    await state.set_state(DealForm.waiting_weight)
    await state.update_data(kind="Продажа")
    await m.answer("🔴 <b>Продажа.</b> Введи вес в граммах (напр. 12.5). Или «отмена».")


@dp.message(DealForm.waiting_weight)
async def deal_weight(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() in CANCEL_WORDS:
        await state.clear()
        return await m.answer("Отменил.", reply_markup=MAIN_KB)
    w = parse_num(m.text)
    if w is None:
        return await m.answer("Не понял вес. Введи число грамм, напр. 12.5")
    await state.update_data(weight=w)
    await state.set_state(DealForm.waiting_price)
    await m.answer(f"Вес {w:g} г. Теперь цена за грамм, ₽ (по которой идёт сделка):")


@dp.message(DealForm.waiting_price)
async def deal_price(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() in CANCEL_WORDS:
        await state.clear()
        return await m.answer("Отменил.", reply_markup=MAIN_KB)
    p = parse_num(m.text)
    if p is None:
        return await m.answer("Не понял цену. Введи число ₽/г, напр. 3900")
    data = await state.get_data()
    await state.clear()
    kind, w = data.get("kind", "Покупка"), data.get("weight", 0)
    await m.answer("Записываю…")
    manual = _load_json(SBER_FILE)
    r = await asyncio.to_thread(rates.build_rates, manual)   # ЦБ 999 + Сбер 585 прод. на дату сделки
    cbr999 = r.get("gold999") or ""
    sber_sell585 = r.get("sber_sell585")
    try:
        res = await asyncio.to_thread(journal.append_deal, kind, w, p, cbr999, sber_sell585)
    except Exception as e:
        return await m.answer(f"⚠️ Не удалось записать в журнал: {e}\n"
                              f"Проверь JOURNAL_BOOK_ID и доступ сервис-аккаунта.",
                              reply_markup=MAIN_KB)
    ab = f"{res['avg_buy']:.0f}" if res["avg_buy"] is not None else "—"
    as_ = f"{res['avg_sell']:.0f}" if res["avg_sell"] is not None else "—"
    sber_txt = f"{sber_sell585:.0f}" if sber_sell585 else "—"
    diff_txt = f"{res['diff']:.0f}" if isinstance(res.get("diff"), (int, float)) else "—"
    emoji = "🟢" if kind == "Покупка" else "🔴"
    lines = [
        f"{emoji} <b>{kind} записана.</b>",
        f"Вес: {w:g} г × {p:.0f} ₽/г = <b>{res['summ']:.0f} ₽</b>",
        f"ЦБ 999: {cbr999 or '—'} ₽/г · Сбер 585 прод.: {sber_txt} ₽/г",
        f"Разница (Сбер 585 − цена): <b>{diff_txt}</b> ₽/г",
    ]
    if isinstance(res.get("sell"), (int, float)):
        lines.append(f"💵 Цена продажи (закуп +{PROFIT_RUB}): <b>{res['sell']:.0f}</b> ₽/г")
    lines += ["", f"📊 Средний курс: покупка <b>{ab}</b> / продажа <b>{as_}</b> ₽/г"]
    await m.answer("\n".join(lines), reply_markup=MAIN_KB)


# ---------------- запуск ----------------
async def main():
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                           disable_notification=True))   # тихие уведомления везде
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(pin_loop(bot))
    asyncio.create_task(news_loop(bot))
    logging.info("GOLD-BOT v1 запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
