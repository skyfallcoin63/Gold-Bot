"""Журнал сделок золота в Google Sheets (gspread, стиль как в scout.py).

Одна книга (config.JOURNAL_BOOK_ID), одна вкладка «Журнал», строки по дням.
Кнопки бота «Купить»/«Продать» -> append_deal(): бот сам считает сумму (вес × цена/г)
и пишет строку. Плюс пишет курс Сбербанка 585 на продажу и разницу (Сбер 585 прод − цена
сделки) — маржа относительно Сбера. Средний курс покупки/продажи (взвешенный: Σ₽ / Σг)
пересчитывается в шапке (K:L) после каждой сделки — локале-независимо, значениями.

Все записываемые ячейки — по центру (правило CLAUDE.md).
"""
import datetime as dt

import config

HEADER = ["Дата", "Тип", "Проба", "ЦБ 999 ₽/г", "Вес, г", "Цена ₽/г", "Сумма ₽",
          "Сбер 585 прод. ₽/г", "Разница ₽/г"]
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
    """rows — строки данных (без шапки). Возвращает (ср.покупка, ср.продажа) —
    взвешенные по граммам: сумма ₽ / сумма грамм (кол. B тип, E вес, G сумма).
    None, если по типу нет сделок."""
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
    """Гарантирует шапку A:I и подписи средних в K:L. Возвращает existing (с шапкой)."""
    head = existing[0] if existing else []
    ok = head and (head[0].strip().lower() == "дата") and len(head) >= 9
    if not ok:
        if ws.col_count < 12:
            ws.add_cols(12 - ws.col_count)
        ws.update(values=[HEADER], range_name="A1:I1", value_input_option="USER_ENTERED")
        ws.format("A1:I1", {**_CENTER, "textFormat": {"bold": True}})
        ws.update(values=[["Ср. курс покупки, ₽/г", ""],
                          ["Ср. курс продажи, ₽/г", ""]],
                  range_name="K1:L2", value_input_option="USER_ENTERED")
        ws.format("K1:L2", _CENTER)
        return [HEADER]
    return existing


def append_deal(kind, weight_g, price_per_g, cbr999, sber_sell585=None, proba=585):
    """Пишет сделку в журнал и пересчитывает средние. Возвращает dict со сводкой.
    kind — 'Покупка' | 'Продажа'. sber_sell585 — курс Сбера 585 на продажу (для разницы)."""
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
    diff = round(float(sber_sell585) - float(price_per_g), 2) if sber_sell585 else ""
    row = [dt.date.today().isoformat(), kind, proba, cbr999, weight_g, price_per_g, summ,
           (sber_sell585 if sber_sell585 else ""), diff]
    start = len(existing) + 1
    ws.update(values=[row], range_name=f"A{start}:I{start}", value_input_option="USER_ENTERED")
    ws.format(f"A{start}:I{start}", _CENTER)

    data = ws.get_all_values()[1:]
    avg_buy, avg_sell = compute_averages(data)
    ws.update(values=[[avg_buy if avg_buy is not None else "—"],
                      [avg_sell if avg_sell is not None else "—"]],
              range_name="L1:L2", value_input_option="USER_ENTERED")
    ws.format("L1:L2", _CENTER)

    return {"summ": summ, "diff": diff, "sber_sell585": sber_sell585,
            "avg_buy": avg_buy, "avg_sell": avg_sell}


# ---------------- self-check (офлайн, только математика) ----------------
if __name__ == "__main__":
    rows = [
        ["2026-08-06", "Покупка", 585, 10500, 10, 3800, 38000, 6285, 2485],
        ["2026-08-06", "Покупка", 585, 10500, 20, 3900, 78000, 6285, 2385],
        ["2026-08-06", "Продажа", 585, 10500, 5, 6200, 31000, 6285, 85],
    ]
    ab, as_ = compute_averages(rows)
    # (38000+78000)/(10+20)=3866.67 ; продажа 31000/5=6200
    assert ab == round((38000 + 78000) / 30, 2), ab
    assert as_ == 6200.0, as_
    # разница = Сбер585прод − цена сделки
    assert round(6285 - 3900, 2) == 2385.0
    assert round(6285 - 6200, 2) == 85.0
    print("✅ compute_averages: ср.покупка", ab, "ср.продажа", as_)
    print("✅ разница (пример): 6285 − 3900 =", round(6285 - 3900, 2), "; 6285 − 6200 =", round(6285 - 6200, 2))
    print("Офлайн-проверка журнала прошла.")
