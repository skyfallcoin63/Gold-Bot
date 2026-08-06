#!/usr/bin/env bash
# Деплой золотого бота: тянет код из GitHub и при изменениях перезапускает сервис gold-bot.
# Путь и сервис захардкожены — скрипт можно запускать из любой папки.
set -euo pipefail

DIR="/root/gold-bot"
SERVICE="gold-bot"
PY="$DIR/venv/bin/python"
FILES="gold_bot.py rates.py news.py journal.py"

cd "$DIR"

echo "→ Проверяю незакоммиченные правки..."
if [ -n "$(git status --porcelain -- $FILES)" ]; then
    echo "⚠️  ВНИМАНИЕ: файлы бота изменены локально и не закоммичены:"
    git status --porcelain -- $FILES
    echo "Разберись с ними (закоммить или откати), потом запускай деплой."
    exit 1
fi

fingerprint() { md5sum $FILES 2>/dev/null | md5sum | awk '{print $1}'; }

BEFORE=$(fingerprint)
echo "→ Забираю свежий код из GitHub..."
git pull --ff-only origin main
AFTER=$(fingerprint)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "✓ Код не изменился — новых правок в GitHub нет. Перезапуск не требуется."
    exit 0
fi

echo "→ Проверяю синтаксис..."
for f in $FILES; do
    "$PY" -m py_compile "$f"
done

echo "→ Перезапускаю $SERVICE..."
systemctl restart "$SERVICE"
sleep 2
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "✗ $SERVICE НЕ поднялся! Последние строки лога:"
    journalctl -u "$SERVICE" -n 30 --no-pager
    exit 1
fi

echo "════════════════════════════════════════"
echo " ГОТОВО. $SERVICE active."
echo "md5:"
md5sum $FILES
echo "Последние строки лога:"
journalctl -u "$SERVICE" -n 5 --no-pager
