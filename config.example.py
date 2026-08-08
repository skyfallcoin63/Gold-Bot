"""Шаблон конфига золотого бота. Скопировать в config.py и заполнить.
config.py и google-key.json в git НЕ коммитятся (см. .gitignore)."""

# --- Telegram ---
TELEGRAM_BOT_TOKEN = "PUT-BOTFATHER-TOKEN-HERE"
GROUP_CHAT_ID = 0            # ID группы для закрепа и новостей (число, у групп отрицательное).
                            # Узнать: добавить бота в группу и отправить /id
ADMIN_IDS = []              # кто может вносить сделки (Купить/Продать). Пусто = разрешено всем.
                            # Узнать свой id: написать боту /id

# --- Google Sheets (журнал) ---
KEY_FILE = "google-key.json"   # ключ сервис-аккаунта postavki-bot@postavki-bot.iam.gserviceaccount.com
JOURNAL_BOOK_ID = ""           # ID книги журнала (расшарить на сервис-аккаунт)
JOURNAL_SHEET = "Журнал"
PROFIT_PER_GRAM = 500          # надбавка к цене закупа для колонки «Цена продажи», ₽/г

# --- Закреп ---
PIN_REFRESH_MINUTES = 30    # как часто бот сам освежает закреплённый курс (минуты, минимум 5)

# --- Новости ---
NEWS_RSS = "https://ru.investing.com/rss/news_11.rss"   # сырьё investing (проверено: доступно с сервера)
NEWS_INTERVAL_HOURS = 4     # как часто постить новости
NEWS_MAX_PER_CYCLE = 3      # не больше N новостей за один заход (без спама)
NEWS_KEYWORDS = ["золот", "серебр", "платин", "паллад", "драгмет",
                 "драгоцен", "унци", "xau"]
NEWS_CTA = ("\n\n💍 Покупаю лом золота (кольца, цепи, серьги) и продаю изделия 585 "
            "по выгодному курсу. Пишите в личку.")
