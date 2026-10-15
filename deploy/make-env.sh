#!/usr/bin/env bash
# Создаёт .env для боевого сервера с надёжными случайными паролями.
#   bash deploy/make-env.sh gpo-lis.ru
# Пароли печатаются один раз — сохраните их в менеджер паролей.
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${1:?Укажите домен: bash deploy/make-env.sh example.ru}"
if [[ -e .env ]]; then echo ".env уже есть — не трогаю. Удалите его, если хотите создать заново."; exit 1; fi

rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
ADMIN_PASSWORD=$(rand 15)
VIEWER_PASSWORD=$(rand 15)

umask 077
cat > .env <<ENV
SITE_ADDRESS=${DOMAIN}, www.${DOMAIN}
SECRET_KEY=${SECRET_KEY}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
VIEWER_PASSWORD=${VIEWER_PASSWORD}
COOKIE_SECURE=1
ENV

echo "Создан .env для ${DOMAIN}"
echo
echo "  Админ (полный доступ):   ${ADMIN_PASSWORD}"
echo "  Наблюдатель (просмотр):  ${VIEWER_PASSWORD}"
echo
echo "Сохраните пароли — больше они нигде не показываются (кроме самого .env на сервере)."
