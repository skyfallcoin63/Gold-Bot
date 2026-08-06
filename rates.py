"""Курсы золота: ЦБ РФ (доллар + золото 999) и Сбербанк (зеркало investzoloto).

Разведка подтверждена на сервере Артёма (Казахстан):
  - ЦБ доллар:  https://www.cbr.ru/scripts/XML_daily.asp        (Valute R01235)  — 200
  - ЦБ золото:  https://www.cbr.ru/scripts/xml_metall.asp       (Record Code=1)  — 200, win-1251, запятая
  - Сбер 999:   https://investzoloto.ru/gold-sber-oms/ (зеркало) — 200, числа в сыром HTML
  - Сбер напрямую (sberbank.ru) с сервера НЕ доступен (гео-блок, connect=000) — только зеркало.

Каждая функция разбита на «загрузку» и «парсинг» — парсинг тестируется офлайн на реальных
образцах (см. self-check внизу файла: `python3 rates.py`).
"""
import datetime as dt
import re
import xml.etree.ElementTree as ET

import requests

PROBA_585 = 0.585
UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 15

CBR_DAILY = "https://www.cbr.ru/scripts/XML_daily.asp"
CBR_METALL = "https://www.cbr.ru/scripts/xml_metall.asp"
SBER_MIRROR = "https://investzoloto.ru/gold-sber-oms/"

USD_CODE = "R01235"       # доллар США в XML_daily
GOLD_CODE = "1"           # золото в xml_metall (Code=1)


def _num(s):
    """'10 502,97' -> 10502.97; пустое/мусор -> None."""
    if s is None:
        return None
    s = str(s).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------- ЦБ: доллар ----------------
def parse_cbr_usd(xml_bytes):
    """Из XML_daily достаёт курс USD/RUB (учитывает номинал). Возвращает float или None."""
    root = ET.fromstring(xml_bytes)
    for val in root.findall("Valute"):
        if val.get("ID") == USD_CODE:
            value = _num(val.findtext("Value"))
            nominal = _num(val.findtext("Nominal")) or 1.0
            if value is not None:
                return round(value / nominal, 4)
    return None


def fetch_cbr_usd():
    r = requests.get(CBR_DAILY, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_cbr_usd(r.content)


# ---------------- ЦБ: золото 999 ----------------
def parse_cbr_gold(xml_bytes):
    """Из xml_metall достаёт записи по золоту (Code=1) -> список (date, price ₽/г),
    отсортированный по дате по возрастанию. Buy==Sell для металлов — берём Buy."""
    root = ET.fromstring(xml_bytes)
    out = []
    for rec in root.findall("Record"):
        if rec.get("Code") != GOLD_CODE:
            continue
        price = _num(rec.findtext("Buy"))
        d = rec.get("Date")  # 'dd.mm.yyyy'
        if price is None or not d:
            continue
        try:
            day = dt.datetime.strptime(d, "%d.%m.%Y").date()
        except ValueError:
            continue
        out.append((day, price))
    out.sort(key=lambda x: x[0])
    return out


def fetch_cbr_gold():
    """Возвращает dict: price999, prev999, pct (изм. за день, %), date (ISO) — по двум
    последним торговым дням. pct=None, если предыдущего дня нет."""
    today = dt.date.today()
    d1 = (today - dt.timedelta(days=14)).strftime("%d/%m/%Y")
    d2 = today.strftime("%d/%m/%Y")
    r = requests.get(CBR_METALL, headers=UA, timeout=TIMEOUT,
                     params={"date_req1": d1, "date_req2": d2})
    r.raise_for_status()
    series = parse_cbr_gold(r.content)
    if not series:
        return None
    last_day, price = series[-1]
    prev = series[-2][1] if len(series) >= 2 else None
    pct = round((price - prev) / prev * 100, 2) if prev else None
    return {"price999": price, "prev999": prev, "pct": pct, "date": last_day.isoformat()}


# ---------------- Сбербанк 999 (зеркало) ----------------
_PRICE_RE = re.compile(r"\d{1,2}[  ]?\d{3}[.,]\d{2}|\d{4,5}[.,]\d{2}")


def parse_sber_html(html_text):
    """Из HTML зеркала берёт первую пару правдоподобных цен золота = сегодняшние покупка/продажа.
    Структура таблицы: Дата -> Покупка -> Продажа (новые сверху). Покупка < Продажа (спред банка).
    Возвращает dict {buy999, sell999} или None."""
    nums = []
    for m in _PRICE_RE.findall(html_text):
        v = _num(m)
        # золото ₽/г — здравый диапазон, чтобы не поймать телефон/год/проценты
        if v is not None and 2000.0 <= v <= 50000.0:
            nums.append(v)
        if len(nums) >= 2:
            break
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]
    buy, sell = min(a, b), max(a, b)   # банк покупает дешевле, продаёт дороже
    return {"buy999": buy, "sell999": sell}


def fetch_sber_gold():
    """Тянет курс Сбера с зеркала. При любой ошибке/смене вёрстки возвращает None
    (бот тогда откатится на ручной ввод)."""
    try:
        r = requests.get(SBER_MIRROR, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_sber_html(r.text)
    except Exception:
        return None


# ---------------- Сводка для закрепа ----------------
def build_rates(sber_manual=None):
    """Собирает всё для закрепа. sber_manual = dict {buy999, sell999} — ручной запас,
    используется, если автоподтяжка Сбера не удалась.
    Возвращает dict с полями и флагом sber_source ('auto'|'manual'|None)."""
    usd = None
    gold = None
    try:
        usd = fetch_cbr_usd()
    except Exception:
        pass
    try:
        gold = fetch_cbr_gold()
    except Exception:
        pass

    sber = fetch_sber_gold()
    sber_source = "auto"
    if not sber and sber_manual:
        sber = dict(sber_manual)
        sber_source = "manual"
    if not sber:
        sber_source = None

    res = {
        "date": (gold or {}).get("date") or dt.date.today().isoformat(),
        "usd": usd,
        "gold999": (gold or {}).get("price999"),
        "gold999_pct": (gold or {}).get("pct"),
        "sber_source": sber_source,
        "sber_buy999": (sber or {}).get("buy999"),
        "sber_sell999": (sber or {}).get("sell999"),
        "sber_buy585": round((sber["buy999"] * PROBA_585), 2) if sber and sber.get("buy999") else None,
        "sber_sell585": round((sber["sell999"] * PROBA_585), 2) if sber and sber.get("sell999") else None,
    }
    return res


def _fmt_rub(x):
    if x is None:
        return "н/д"
    return f"{x:,.0f}".replace(",", " ")


def _fmt_pct(pct):
    if pct is None:
        return ""
    arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "➡️")
    return f"  {arrow}{abs(pct):.1f}% за день"


def render_pin(r):
    """Текст закреплённого сообщения для группы."""
    d = r["date"]
    try:
        d = dt.date.fromisoformat(d).strftime("%d.%m.%Y")
    except Exception:
        pass
    lines = [f"💵 <b>{r['usd']:.2f} ₽</b>" if r["usd"] else "💵 н/д"]
    g = _fmt_rub(r["gold999"])
    lines.append(f"🥇 999 (ЦБ): <b>{g} ₽/г</b>{_fmt_pct(r['gold999_pct'])}")
    if r["sber_source"]:
        lines.append(f"🏦 Сбер 999: покупка <b>{_fmt_rub(r['sber_buy999'])}</b> / "
                     f"продажа <b>{_fmt_rub(r['sber_sell999'])}</b> ₽/г")
        lines.append(f"🏦 Сбер 585: покупка <b>{_fmt_rub(r['sber_buy585'])}</b> / "
                     f"продажа <b>{_fmt_rub(r['sber_sell585'])}</b> ₽/г")
        if r["sber_source"] == "manual":
            lines.append("<i>(Сбер — ручной ввод)</i>")
    else:
        lines.append("🏦 Сбер: н/д (кнопка «🏦 Курс Сбера»)")
    lines.append(f"<i>Курс на {d} (ЦБ РФ)</i>")
    return "\n".join(lines)


# ---------------- анализ рынка (золото + серебро, история ЦБ) ----------------
METAL_CODES = {"gold": "1", "silver": "2"}   # коды металлов в xml_metall
METAL_TITLE = {"gold": "🥇 Золото", "silver": "🥈 Серебро"}


def parse_metal_series(xml_bytes, code):
    """Из xml_metall достаёт ряд (date, price ₽/г) по коду металла, сортировка по дате."""
    root = ET.fromstring(xml_bytes)
    out = []
    for rec in root.findall("Record"):
        if rec.get("Code") != code:
            continue
        price = _num(rec.findtext("Buy"))
        d = rec.get("Date")
        if price is None or not d:
            continue
        try:
            day = dt.datetime.strptime(d, "%d.%m.%Y").date()
        except ValueError:
            continue
        out.append((day, price))
    out.sort(key=lambda x: x[0])
    return out


def fetch_metals_history(days=180):
    """Один запрос xml_metall за период -> {'gold': [...], 'silver': [...]}."""
    today = dt.date.today()
    d1 = (today - dt.timedelta(days=days)).strftime("%d/%m/%Y")
    d2 = today.strftime("%d/%m/%Y")
    r = requests.get(CBR_METALL, headers=UA, timeout=TIMEOUT,
                     params={"date_req1": d1, "date_req2": d2})
    r.raise_for_status()
    xml = r.content
    return {name: parse_metal_series(xml, code) for name, code in METAL_CODES.items()}


def _pct(cur, old):
    return round((cur - old) / old * 100, 1) if old else None


def analyze_series(series, horizons=(7, 30, 90, 180)):
    """Метрики по ряду: текущая цена, изменение за горизонты (%), диапазон и позиция в нём."""
    if not series:
        return None
    last_date, cur = series[-1]

    def ago(n):
        target = last_date - dt.timedelta(days=n)
        prev = None
        for d, p in series:
            if d <= target:
                prev = p
            else:
                break
        return prev

    changes = {n: _pct(cur, ago(n)) for n in horizons}
    lo = min(p for _, p in series)
    hi = max(p for _, p in series)
    pos = round((cur - lo) / (hi - lo) * 100) if hi > lo else 50
    return {"cur": cur, "changes": changes, "lo": lo, "hi": hi, "pos": pos}


def _market_block(title, a):
    if not a:
        return f"{title}: н/д"
    ch = a["changes"]

    def f(n):
        v = ch.get(n)
        return "—" if v is None else f"{v:+.1f}%"

    t90 = ch.get(90) or 0
    trend = "📈 рост" if t90 > 1 else ("📉 снижение" if t90 < -1 else "➡️ вбок")
    rng = a["hi"] - a["lo"]
    entry_zone = a["lo"] + rng / 3        # нижняя треть диапазона ЦБ — зона закупа
    exit_zone = a["hi"] - rng / 3         # верхняя треть — зона выхода
    mom7 = ch.get(7) or 0        # свежий импульс за неделю
    pos = a["pos"]
    if pos <= 33:
        sig = ("🟢 Сигнал: разворот у низа — можно заходить" if mom7 > 0
               else "🟡 Сигнал: низко, но ещё падает — подождать, пока 7д не станет плюсовым")
    elif pos <= 66:
        sig = "🟡 Сигнал: середина диапазона — ждать ближе к низу"
    else:
        sig = ("🔴 Сигнал: дорого и пошло вниз — не заходить, время продавать" if mom7 < 0
               else "🔴 Сигнал: дорого — входить осторожно, готовить продажу")
    return (f"<b>{title}</b>\n"
            f"Сейчас: <b>{_fmt_rub(a['cur'])} ₽/г</b> ({pos}% от низа диапазона)\n"
            f"7д {f(7)} · 30д {f(30)} · 90д {f(90)} · 180д {f(180)}\n"
            f"Диапазон 180д: {_fmt_rub(a['lo'])}–{_fmt_rub(a['hi'])} ₽/г\n"
            f"Тренд (90д): {trend}\n"
            f"📥 Заходить (закуп) ниже: <b>{_fmt_rub(entry_zone)} ₽/г</b>\n"
            f"📤 Выходить (продажа) выше: <b>{_fmt_rub(exit_zone)} ₽/г</b>\n"
            f"{sig}")


def render_market(days=180):
    """Текст «Анализ рынка» по золоту и серебру (999) на данных ЦБ за период."""
    hist = fetch_metals_history(days)
    g = analyze_series(hist.get("gold", []))
    s = analyze_series(hist.get("silver", []))
    parts = ["📊 <b>Анализ рынка</b> (ЦБ РФ, 999 проба, 180 дней)", "",
             _market_block("🥇 Золото", g), "", _market_block("🥈 Серебро", s)]
    parts += ["", "<i>Цены заход/выход — нижняя и верхняя трети диапазона ЦБ за 180 дней. "
                  "Это ориентир по историческим данным, не прогноз: гарантий по «дну» нет, "
                  "смотри ещё и на тренд.</i>"]
    return "\n".join(parts)


# ---------------- self-check (офлайн, на реальных образцах) ----------------
if __name__ == "__main__":
    daily = (b'<?xml version="1.0" encoding="windows-1251"?>'
             b'<ValCurs Date="10.07.2025" name="Foreign Currency Market">'
             b'<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode>'
             b'<Nominal>1</Nominal><Name>Doll</Name><Value>78,1727</Value>'
             b'<VunitRate>78,1727</VunitRate></Valute></ValCurs>')
    metall = ('<?xml version="1.0" encoding="windows-1251"?>'
              '<Metall FromDate="20260801" ToDate="20260806" name="Precious metals quotations">'
              '<Record Date="04.08.2026" Code="1"><Buy>10490,00</Buy><Sell>10490,00</Sell></Record>'
              '<Record Date="05.08.2026" Code="1"><Buy>10502,97</Buy><Sell>10502,97</Sell></Record>'
              '<Record Date="05.08.2026" Code="2"><Buy>148,32</Buy><Sell>148,32</Sell></Record>'
              '</Metall>').encode("cp1251")
    sber_html = ('<table><tr><td>04.08.2026</td><td>10 109,00</td><td>↑</td>'
                 '<td>10 743,00</td><td>↑</td></tr>'
                 '<tr><td>03.08.2026</td><td>9 933,00</td><td>10 557,00</td></tr></table>')

    assert parse_cbr_usd(daily) == 78.1727, parse_cbr_usd(daily)
    g = parse_cbr_gold(metall)
    assert g[-1] == (dt.date(2026, 8, 5), 10502.97), g
    assert len(g) == 2, g
    pct = round((10502.97 - 10490.00) / 10490.00 * 100, 2)
    s = parse_sber_html(sber_html)
    assert s == {"buy999": 10109.0, "sell999": 10743.0}, s
    assert abs(s["sell999"] * PROBA_585 - 6284.655) < 0.01
    print("✅ parse_cbr_usd:", parse_cbr_usd(daily), "₽")
    print("✅ parse_cbr_gold:", g[-1], "| изм. за день:", pct, "%")
    print("✅ parse_sber_html:", s, "| 585 продажа:", round(s["sell999"] * PROBA_585, 2))

    # анализ рынка (синтетический ряд)
    hist_xml = ('<?xml version="1.0" encoding="windows-1251"?><Metall>'
                '<Record Date="01.02.2026" Code="1"><Buy>9000,00</Buy><Sell>9000,00</Sell></Record>'
                '<Record Date="01.05.2026" Code="1"><Buy>10000,00</Buy><Sell>10000,00</Sell></Record>'
                '<Record Date="06.08.2026" Code="1"><Buy>10500,00</Buy><Sell>10500,00</Sell></Record>'
                '<Record Date="06.08.2026" Code="2"><Buy>150,00</Buy><Sell>150,00</Sell></Record>'
                '</Metall>').encode("cp1251")
    ser = parse_metal_series(hist_xml, "1")
    a = analyze_series(ser, horizons=(90, 180))
    assert a["cur"] == 10500.0 and a["lo"] == 9000.0 and a["hi"] == 10500.0, a
    assert a["pos"] == 100, a           # текущая = максимум -> верх диапазона
    assert a["changes"][90] == round((10500 - 10000) / 10000 * 100, 1), a
    print("✅ analyze_series:", a)
    print("Все офлайн-проверки парсинга прошли.")
