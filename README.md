# Долгожданный Трэш: Сумеречный лес — сайт регистрации

Flask + SQLite. Одна страница с формой и админка по адресу `/admin`:

- пароль `ADMIN_PASSWORD` — полный доступ: отметка «пришёл», удаление, открытие/закрытие регистрации, выгрузка в Excel (лист «Участники» с фильтрами и лист «Сводка» с явкой по факультетам и курсам);
- пароль `VIEWER_PASSWORD` — только просмотр списка: без изменений и без выгрузки.

## Запуск локально

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ADMIN_PASSWORD=test VIEWER_PASSWORD=look .venv/bin/python app.py   # http://localhost:5050
# FLASK_DEBUG=1 — включить автоперезагрузку и отладчик (только локально!)
```

## Где что менять

- Название, дата, место, расписание, варианты ответов — в начале `app.py` (`EVENT`, `COURSES`, `READINESS`).
- Организаторы (логотип и ссылка на группу ВК) — `ORGANIZERS` в `app.py`, логотипы в `static/img/logo-*.webp`.
- Цвета и шрифты — переменные в начале `static/style.css`.
- Картинки — `static/img/`: сжатые до webp версии файлов из `assets/` (исходники на сервер копировать не нужно).
- Тексты страниц — `templates/`.

## Безопасность

Что уже встроено:

| Угроза | Защита |
|---|---|
| Прокликивание кнопки, повторные отправки | кнопка блокируется после первого клика; сервер держит паузу **5 с** между отправками с одного устройства и **30 с** после успешной заявки (`SUBMIT_COOLDOWN`, `AFTER_SUCCESS_COOLDOWN` в `app.py`) |
| Скрипты, которые шлют заявки потоком | лимиты по IP: 20 в минуту и 200 за 10 минут (мягкие — в Wi-Fi вуза сотни людей с одного IP); при установке без Docker nginx добавляет ещё 30 запросов/мин на форму |
| Боты | CSRF-токен, ловушка по времени (форма не отправится быстрее 3 с после открытия) и скрытое поле-приманка |
| Подбор пароля админки | 10 попыток за 15 минут с IP; в nginx — 10 запросов/мин на вход |
| Excel-инъекция (`=HYPERLINK(...)` в поле формы) | всё, что ввёл участник, пишется в Excel только как текст |
| XSS, кликджекинг | строгая Content-Security-Policy без inline-скриптов, `X-Frame-Options: DENY`, экранирование в шаблонах |
| Кража/подделка сессии | cookie `__Host-session` с `Secure`, `HttpOnly`, `SameSite=Lax`; вход живёт 12 часов; новая сессия при входе; смена пароля в `.env` выкидывает все старые входы этой роли |
| Утечка данных | админка `no-store` (не кешируется), `noindex`, `no-referrer`; наблюдатель не может ни менять, ни выгружать |
| Подмена IP через заголовок | `X-Real-IP` принимается только от своего прокси (Caddy в Docker-сети или nginx на 127.0.0.1, настройка `TRUSTED_PROXIES`) |
| Слабые настройки | при `COOKIE_SECURE=1` сайт не запустится, если `SECRET_KEY` < 32 символов или пароли < 12 |
| Медленные/огромные запросы | таймауты и лимиты размера в nginx и gunicorn, тело запроса ≤ 32 КБ |
| Взлом через приложение | контейнер без root, только для чтения, без capabilities (или systemd-песочница при установке без Docker) |

Сгенерировать надёжные значения для `.env`:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32)); print('ADMIN_PASSWORD=' + secrets.token_urlsafe(18)); print('VIEWER_PASSWORD=' + secrets.token_urlsafe(18))"
```

Подозрительная активность пишется в `logs/app.log` (см. раздел «Логи»).

## Развёртывание в Docker (рекомендуется)

Два контейнера: `app` (сайт) и `caddy` (HTTPS-сертификат Let's Encrypt получает и продлевает сам, отдаёт статику).
База лежит в томе `lis-data` и переживает пересборку и обновления.

Хватит самого дешёвого VPS: 1 vCPU, 1 ГБ RAM, Ubuntu 22.04/24.04.

1. Указать A-запись домена на IP сервера.
2. На сервере:

```bash
# Docker (официальный скрипт) и файрвол
curl -fsSL https://get.docker.com | sudo sh
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw --force enable

# скопировать проект с вашего компьютера (без .venv, data и assets):
#   rsync -av --exclude .venv --exclude data --exclude assets --exclude .claude ./ user@IP:~/lis_site/
cd ~/lis_site
cp .env.example .env
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32)); print('ADMIN_PASSWORD=' + secrets.token_urlsafe(18)); print('VIEWER_PASSWORD=' + secrets.token_urlsafe(18))"
nano .env        # SITE_ADDRESS=ваш-домен.ru, вставить значения выше, COOKIE_SECURE=1
chmod 600 .env
mkdir -p logs && sudo chown 10001:10001 logs   # папка для логов, пишет в неё пользователь контейнера

sudo docker compose up -d --build
```

3. Через минуту открыть `https://ваш-домен.ru` — сертификат уже будет. Проверить регистрацию и `/admin`.

> Docker сам открывает порты 80/443 в обход ufw — это нормально, наружу смотрят только они; сайт (порт 8000) доступен лишь Caddy внутри Docker-сети.

### Логи

Сайт пишет логи в папку `logs/` рядом с проектом (файлы ротируются: по 5 МБ, хранится 5 штук):

- `logs/access.log` — каждый запрос одной строкой: время, IP посетителя, метод и адрес, статус, размер, время обработки, браузер;
- `logs/app.log` — входы в админку, неверные пароли, срабатывание лимитов и ловушек для ботов.

```bash
tail -f logs/access.log                 # смотреть запросы в реальном времени
grep WARNING logs/app.log               # подозрительная активность
grep '"POST /register" 302' logs/access.log | wc -l   # сколько успешных заявок
```

Данные из формы и пароли в логи не попадают.

### Если сайт упал

Caddy показывает посетителям страницу «Лес временно закрыт» (`static/errors/unavailable.html`), она сама обновляется каждые 30 секунд. Контейнер с сайтом Docker перезапускает автоматически. Если не поднимается — смотрите `sudo docker compose logs app`.

### Полезные команды

Из папки проекта:

```bash
sudo docker compose ps                          # статус (app должен быть healthy)
sudo docker compose logs -f app                 # вывод контейнера (ошибки запуска и т.п.)
sudo docker compose up -d --build              # обновить сайт (после загрузки новых файлов)
sudo docker compose restart app                 # перезапустить после правки .env

# бэкап базы на сервер (целостная копия, даже во время работы сайта)
sudo docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/app/data/registrations.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d); d.close()"
sudo docker compose cp app:/app/data/backup.db ./backup-$(date +%F).db
sudo docker compose exec app rm /app/data/backup.db
```

Перенести уже собранную базу (например, с локальной машины) в контейнер:

```bash
sudo docker compose cp ./registrations.db app:/app/data/registrations.db && sudo docker compose restart app
```

Проверить без домена (локально или по IP): в `.env` поставить `SITE_ADDRESS=:80` и `COOKIE_SECURE=0`, сайт будет на `http://localhost`.

> **Важно:** `docker compose down -v` удаляет тома вместе с базой участников. Для остановки используйте `docker compose down` без `-v`.

Безопасность контейнеров: сайт работает от непривилегированного пользователя, файловая система только для чтения (писать можно лишь в том с базой), все Linux-capabilities сняты, лимиты памяти и процессов, логи ротируются.

## Развёртывание без Docker (nginx + systemd)

Хватит самого дешёвого тарифа: 1 vCPU, 512 МБ–1 ГБ RAM.

1. Указать A-запись домена на IP сервера.
2. На сервере:

```bash
sudo apt update && sudo apt install -y python3-venv nginx certbot python3-certbot-nginx ufw unattended-upgrades

# файрвол: открыты только SSH и веб
sudo ufw allow OpenSSH && sudo ufw allow "Nginx Full" && sudo ufw --force enable

sudo mkdir -p /opt/trash-forest && sudo chown $USER /opt/trash-forest
# скопировать файлы проекта (с вашего компьютера; папку assets и .venv копировать не нужно):
#   scp -r app.py requirements.txt templates static deploy .env.example user@IP:/opt/trash-forest/
cd /opt/trash-forest
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # вставить значения из команды выше, COOKIE_SECURE=1

# код принадлежит root и только читается; писать сайт может лишь в data/
sudo chown -R root:root /opt/trash-forest
sudo mkdir -p data logs && sudo chown www-data:www-data data logs && sudo chmod 700 data
sudo chown root:www-data .env && sudo chmod 640 .env

sudo cp deploy/trash-forest.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now trash-forest

sudo rm -f /etc/nginx/sites-enabled/default    # иначе конфликт default_server
sudo cp deploy/nginx.conf /etc/nginx/sites-available/trash-forest   # поправить server_name
sudo ln -s /etc/nginx/sites-available/trash-forest /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d ваш-домен.ru --redirect    # HTTPS и редирект с http
```

3. Проверить: открыть сайт, зарегистрироваться, зайти в `/admin`, удалить тестовую запись.

Совет по SSH: входите по ключу, а вход по паролю отключите (`PasswordAuthentication no` в `/etc/ssh/sshd_config`).

После обновления кода на сервере: `.venv/bin/pip install -r requirements.txt && sudo systemctl restart trash-forest` (новые колонки в базе добавятся сами).

## Бэкап

Все данные хранятся в одном файле: `/opt/trash-forest/data/registrations.db`. Перед мероприятием и после него скачайте Excel из админки.
# lis-site
