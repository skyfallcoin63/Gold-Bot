"""Новости про драгметаллы: RSS investing (сырьё) -> фильтр по ключевым словам -> дедуп.

Источник проверен на сервере Артёма: https://ru.investing.com/rss/news_11.rss — 200, золото в ленте есть.
Разделение fetch/parse: парсинг и фильтр тестируются офлайн (`python3 news.py`).
Дедуп — по guid/ссылке, хранится в state/news_seen.json (в git не коммитится).
"""
import html as html_lib
import json
import os
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 15


def parse_rss(xml_bytes):
    """RSS -> список dict {title, link, guid, pubdate, desc}. Порядок как в ленте (новые сверху)."""
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    items = []
    for it in (channel.findall("item") if channel is not None else root.findall(".//item")):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link).strip()
        pub = (it.findtext("pubDate") or "").strip()
        desc = (it.findtext("description") or "").strip()
        if title:
            items.append({"title": title, "link": link, "guid": guid,
                          "pubdate": pub, "desc": desc})
    return items


def matches(item, keywords):
    """True, если заголовок или описание содержат хоть одно ключевое слово (регистронезависимо)."""
    hay = (item["title"] + " " + item.get("desc", "")).lower()
    return any(kw.lower() in hay for kw in keywords)


def filter_precious(items, keywords):
    return [it for it in items if matches(it, keywords)]


def _load_seen(path):
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_seen(path, seen):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # держим последние 500 guid, чтобы файл не пух
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-500:], f, ensure_ascii=False)


def fetch_rss(url):
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_rss(r.content)


def select_new(url, keywords, seen_path, limit):
    """Тянет ленту, фильтрует по драгметаллам, отбрасывает уже отправленные, помечает новые
    отправленными и возвращает не больше limit самых свежих. При ошибке сети -> []."""
    try:
        items = fetch_rss(url)
    except Exception:
        return []
    matched = filter_precious(items, keywords)
    seen = _load_seen(seen_path)
    fresh = [it for it in matched if it["guid"] not in seen]
    fresh = fresh[:int(limit)]
    if fresh:
        for it in fresh:
            seen.add(it["guid"])
        _save_seen(seen_path, seen)
    return fresh


def format_news(item, cta):
    """HTML-текст новости для группы: жирный заголовок + ссылка + строка-призыв."""
    title = html_lib.escape(item["title"])
    link = item.get("link", "")
    body = f"📰 <b>{title}</b>"
    if link:
        body += f'\n<a href="{html_lib.escape(link)}">Читать</a>'
    body += cta
    return body


# ---------------- self-check (офлайн) ----------------
if __name__ == "__main__":
    sample = ('<?xml version="1.0" encoding="utf-8"?><rss><channel>'
              '<item><title>Золото перекуплено у сопротивления Фибо</title>'
              '<link>https://ru.investing.com/a1</link><guid>a1</guid>'
              '<description>Технический анализ золота</description></item>'
              '<item><title>Газ в Европе вырос на 2%</title>'
              '<link>https://ru.investing.com/a2</link><guid>a2</guid>'
              '<description>Природный газ</description></item>'
              '<item><title>Серебро дорожает вслед за золотом</title>'
              '<link>https://ru.investing.com/a3</link><guid>a3</guid>'
              '<description>Драгметаллы растут</description></item>'
              '</channel></rss>').encode("utf-8")
    kws = ["золот", "серебр", "платин", "паллад", "унци"]
    items = parse_rss(sample)
    assert len(items) == 3, items
    m = filter_precious(items, kws)
    assert [i["guid"] for i in m] == ["a1", "a3"], m   # газ отфильтрован
    msg = format_news(m[0], "\n\nПишите в личку.")
    assert "Золото" in msg and "Читать" in msg and "Пишите" in msg
    print("✅ parse_rss:", len(items), "новостей")
    print("✅ filter_precious оставил:", [i["title"] for i in m])
    print("✅ format_news:\n" + msg)
    print("Все офлайн-проверки новостей прошли.")
