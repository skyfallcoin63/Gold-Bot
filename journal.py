"""Журнал сделок золота в Google Sheets (gspread, стиль как в scout.py).

Одна книга (config.JOURNAL_BOOK_ID), одна вкладка «Журнал», строки по дням.
Кнопки бота «Купить»/«Продать» -> append_deal(): бот сам считает сумму (вес × цена/г)
и пишет строку. Средний курс покупки/продажи (взвешенный: Σ₽ / Σг) пересчитывается
в шапке (I:J) после каждой сделки — локале-независимо, значениями, а не формулами.

Все записываемые ячейки — по центру (правило CLAUDE.md).
"""
import datetime as dt

import config

HEADER = ["Дата", "Тип", "Проба", "ЦБ 999 ₽/г", "Вес, г", "Цена ₽/г", "Сумма ₽"]
_CENTER = {"horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"}


def _num(s):
    if s is None:
        return None
    s = str(s).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def compute_averages(rows):
    """rows — строки данных A:G (без шапки). Возвращает (ср.покупка, ср.продажа) —
    взвешенные по граммам: сумма ₽ / сумма грамм. None, если по типу нет сделок."""
    def wavg(kind):
        tot_sum = tot_g = 0.0
        for r in rows:
            if len(r) > 6 and (r[1] or "").strip() == kind:
                g, s = _num(r[4]), _num(r[6])
                if g and s:
                    tot_g += g
                    tot_sum += s
        return round(tot_sum / tot_g, 2) if tot_g else None
    return wavg("Покупка"), wavg("Продажа")


def _open_ws(book, name):
    import gspread
    target = name.strip()
    for ws in book.worksheets():
        if ws.title.strip() == target:
            return ws
    raise gspread.WorksheetNotFound(name)


def _ensure_header(ws, existing):
    """Гарантирует шапку A:G и подписи средних в I:J. Возвращает existing (с шапкой)."""
    import gspread  # noqa: F401
    head = existing[0] if existing else []
    if not head or (head[0].strip().lower() if head else "") != "дата":
        ws.update(values=[HEADER], range_name="A1:G1", value_input_option="USER_ENTERED")
        ws.format("A1:G1", {**_CENTER, "textFormat": {"bold": True}})
        ws.update(values=[["Ср. курс покупки, ₽/г", ""],
                          ["Ср. курс продажи, ₽/г", ""]],
                  range_name="I1:J2", value_input_option="USER_ENTERED")
        ws.format("I1:J2", _CENTER)
        return [HEADER]
    return existing


def append_deal(kind, weight_g, price_per_g, cbr999, proba=585):
    """Пишет сделку в журнал и пересчитывает средние. Возвращает dict со сводкой.
    kind — 'Покупка' | 'Продажа'."""
    import gspread
    if not getattr(config, "KEY_FILE", None) or not getattr(config, "JOURNAL_BOOK_ID", ""):
        raise RuntimeError("нет config.KEY_FILE / JOURNAL_BOOK_ID — журнал не настроен")
    gc = gspread.service_account(filename=config.KEY_FILE)
    book = gc.open_by_key(config.JOURNAL_BOOK_ID)
    name = getattr(config, "JOURNAL_SHEET", "Журнал")
    try:
        ws = _open_ws(book, name)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=name, rows=1000, cols=12)

    existing = _ensure_header(ws, ws.get_all_values())

    summ = round(float(weight_g) * float(price_per_g), 2)
    row = [dt.date.today().isoformat(), kind, proba, cbr999, weight_g, price_per_g, summ]
    start = len(existing) + 1
    ws.update(values=[row], range_name=f"A{start}:G{start}", value_input_option="USER_ENTERED")
    ws.format(f"A{start}:G{start}", _CENTER)

    data = ws.get_all_values()[1:]
    avg_buy, avg_sell = compute_averages(data)
    ws.update(values=[[avg_buy if avg_buy is not None else "—"],
                      [avg_sell if avg_sell is not None else "—"]],
              range_name="J1:J2", value_input_option="USER_ENTERED")
    ws.format("J1:J2", _CENTER)

    return {"summ": summ, "avg_buy": avg_buy, "avg_sell": avg_sell}


# ---------------- self-check (офлайн, только математика) ----------------
if __name__ == "__main__":
    rows = [
        ["2026-08-06", "Покупка", 585, 10500, 10, 3800, 38000],
        ["2026-08-06", "Покупка", 585, 10500, 20, 3900, 78000],
        ["2026-08-06", "Продажа", 585, 10500, 5, 6200, 31000],
    ]
    ab, as_ = compute_averages(rows)
    # (38000+78000)/(10+20)=3866.67 ; продажа 31000/5=6200
    assert ab == round((38000 + 78000) / 30, 2), ab
    assert as_ == 6200.0, as_
    assert round(12.5 * 4000, 2) == 50000.0
    print("✅ compute_averages: ср.покупка", ab, "ср.продажа", as_)
    print("Офлайн-проверка журнала прошла.")
