"""Журнал сделок золота в Google Sheets (gspread, стиль как в scout.py).

Одна книга (config.JOURNAL_BOOK_ID), одна вкладка «Журнал», строки по дням.
Кнопки бота «Купить»/«Продать» -> append_deal(): бот считает сумму (вес × цена/г) и пишет строку.

Пересчёт — ФОРМУЛАМИ (чтобы ручные правки в таблице подхватывались):
  - «Разница ₽/г» (I) = Сбер 585 прод − Цена;
  - «Цена продажи +N ₽/г» (J) = Цена + N (заработок N ₽/г, только для «Покупка»); N = config.PROFIT_PER_GRAM;
  - средний курс покупки/продажи (N:O) — формулы по колонкам (взвешенные Σ₽ / Σг).
Разделитель аргументов формул зависит от локали книги (ru → «;», en → «,»).

Все записываемые ячейки — по центру (правило CLAUDE.md).
"""
import datetime as dt

import config

PROFIT = int(getattr(config, "PROFIT_PER_GRAM", 500))   # надбавка к цене закупа для цены продажи, ₽/г
HEADER = ["Дата", "Тип", "Проба", "ЦБ 999 ₽/г", "Вес, г", "Цена ₽/г", "Сумма ₽",
          "Сбер 585 прод. ₽/г", "Разница ₽/г", f"Цена продажи +{PROFIT} ₽/г"]
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
    """rows — строки данных (без шапки). (ср.покупка, ср.продажа) — взвешенные по граммам
    (кол. B тип, E вес, G сумма). Только для текста ответа боту (в таблице — формулы)."""
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


def _arg_sep(book):
    """Разделитель аргументов формул по локали книги: ru/de/fr… → «;», en → «,»."""
    try:
        loc = book.fetch_sheet_metadata().get("properties", {}).get("locale", "")
    except Exception:
        loc = ""
    return "," if str(loc).lower().startswith("en") else ";"


def _avg_formula(sep, kind):
    return (f'=IFERROR(SUMIFS(G2:G{sep}B2:B{sep}"{kind}")'
            f'/SUMIFS(E2:E{sep}B2:B{sep}"{kind}"){sep}"—")')


def _diff_formula(sep, row):
    """Разница строки: Сбер 585 прод (H) − Цена (F)."""
    return f'=IF(AND(H{row}<>""{sep}F{row}<>""){sep}H{row}-F{row}{sep}"")'


def _sell_formula(sep, row):
    """Цена продажи: Цена (F) + PROFIT — только для строк «Покупка»."""
    return f'=IF(B{row}="Покупка"{sep}F{row}+{PROFIT}{sep}"")'


def _ensure_header(ws, existing, sep):
    """Гарантирует актуальную шапку A:J, формулы средних в O1:O2 и подписи в N1:N2.
    Если разметка старая — переписывает и чистит старую сводку (K:L). Возвращает existing."""
    head = existing[0] if existing else []
    if [str(c).strip() for c in head[:10]] != HEADER:
        if ws.col_count < 15:
            ws.add_cols(15 - ws.col_count)
        ws.update(values=[HEADER], range_name="A1:J1", value_input_option="USER_ENTERED")
        ws.format("A1:J1", {**_CENTER, "textFormat": {"bold": True}})
        ws.update(values=[["Ср. курс покупки, ₽/г", _avg_formula(sep, "Покупка")],
                          ["Ср. курс продажи, ₽/г", _avg_formula(sep, "Продажа")]],
                  range_name="N1:O2", value_input_option="USER_ENTERED")
        ws.format("N1:O2", _CENTER)
        try:
            ws.batch_clear(["K1:L2"])   # убрать старую сводку прежних разметок
        except Exception:
            pass
        if not existing:
            return [HEADER]
        existing[0] = list(HEADER)
        return existing
    return existing


def append_deal(kind, weight_g, price_per_g, cbr999, sber_sell585=None, proba=585):
    """Пишет сделку в журнал (разница, цена продажи и средние — формулами). Возвращает dict."""
    import gspread
    if not getattr(config, "KEY_FILE", None) or not getattr(config, "JOURNAL_BOOK_ID", ""):
        raise RuntimeError("нет config.KEY_FILE / JOURNAL_BOOK_ID — журнал не настроен")
    gc = gspread.service_account(filename=config.KEY_FILE)
    book = gc.open_by_key(config.JOURNAL_BOOK_ID)
    sep = _arg_sep(book)
    name = getattr(config, "JOURNAL_SHEET", "Журнал")
    try:
        ws = _open_ws(book, name)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=name, rows=1000, cols=15)

    existing = _ensure_header(ws, ws.get_all_values(), sep)

    summ = round(float(weight_g) * float(price_per_g), 2)
    start = len(existing) + 1
    row = [dt.date.today().isoformat(), kind, proba, cbr999, weight_g, price_per_g, summ,
           (sber_sell585 if sber_sell585 else ""),
           _diff_formula(sep, start), _sell_formula(sep, start)]
    ws.update(values=[row], range_name=f"A{start}:J{start}", value_input_option="USER_ENTERED")
    ws.format(f"A{start}:J{start}", _CENTER)

    diff = (round(float(sber_sell585) - float(price_per_g), 2) if sber_sell585 else "")
    sell = round(float(price_per_g) + PROFIT, 2) if kind == "Покупка" else ""
    data = ws.get_all_values()[1:]
    avg_buy, avg_sell = compute_averages(data)
    return {"summ": summ, "diff": diff, "sell": sell, "sber_sell585": sber_sell585,
            "avg_buy": avg_buy, "avg_sell": avg_sell}


# ---------------- self-check (офлайн, только математика) ----------------
if __name__ == "__main__":
    rows = [
        ["2026-08-06", "Покупка", 585, 10500, 10, 3800, 38000, 6285, 2485, 4300],
        ["2026-08-06", "Покупка", 585, 10500, 20, 3900, 78000, 6285, 2385, 4400],
        ["2026-08-06", "Продажа", 585, 10500, 5, 6200, 31000, 6285, 85, ""],
    ]
    ab, as_ = compute_averages(rows)
    assert ab == round((38000 + 78000) / 30, 2), ab
    assert as_ == 6200.0, as_
    assert _avg_formula(";", "Покупка") == '=IFERROR(SUMIFS(G2:G;B2:B;"Покупка")/SUMIFS(E2:E;B2:B;"Покупка");"—")'
    assert _diff_formula(";", 2) == '=IF(AND(H2<>"";F2<>"");H2-F2;"")'
    assert _sell_formula(";", 2) == '=IF(B2="Покупка";F2+500;"")'
    assert _sell_formula(",", 3) == '=IF(B3="Покупка",F3+500,"")'
    print("✅ compute_averages: ср.покупка", ab, "ср.продажа", as_)
    print("✅ формула цены продажи:", _sell_formula(";", 2), "(PROFIT =", PROFIT, ")")
    print("Офлайн-проверка журнала прошла.")
