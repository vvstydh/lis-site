# Обновление работающего сайта

Архив релиза содержит `app.py`, `Dockerfile`, `requirements.txt`, `templates/` и `static/`. Для обновления используются действующие настройки сервера, включая `docker-compose.yml`, `deploy/Caddyfile`, `.env`, сертификаты и том базы.

## Docker Compose и Caddy

1. Загрузите архив с Mac в домашнюю папку пользователя на сервере. В командах ниже архив лежит по пути `~/lis-site-release.tar.gz`.
2. По SSH перейдите в **ту же папку**, из которой развернули старый сайт. Если при запуске передавали имя Compose-проекта через `-p` или список файлов через `-f`, используйте те же параметры во всех командах.

   ```bash
   cd ~/lis_site
   sudo docker compose ps
   sudo docker compose config --images
   ```

   Для конфигурации из этого репозитория в списке должны быть работающие `app` и `caddy`, а приложение использует образ `lis-site:latest`. Если образ переименован, подставьте его точное имя с тегом вместо `lis-site:latest` ниже. Сохраняйте текущую папку проекта и именованный том базы. Команда `docker compose down -v` удаляет том.

3. Сохраните прежние файлы, образ и согласованную копию SQLite. Выполняйте следующие блоки в том же сеансе SSH, чтобы сохранились переменные.

   ```bash
   umask 077
   stamp=$(date +%Y%m%d-%H%M%S)
   backup_dir="$HOME/lis-site-backups/$stamp"
   mkdir -p "$backup_dir"
   tar -czf "$backup_dir/site-files.tar.gz" app.py Dockerfile requirements.txt templates static

   old_container=$(sudo docker compose ps -q app)
   old_image=$(sudo docker inspect --format '{{.Image}}' "$old_container")
   sudo docker image tag "$old_image" "lis-site:before-$stamp"
   printf '%s\n' "lis-site:before-$stamp" > "$backup_dir/image-tag"

   sudo docker compose exec -T app python - > "$backup_dir/registrations.db" <<'PY'
   import os
   import shutil
   import sqlite3
   import sys
   import tempfile
   from contextlib import closing
   from pathlib import Path

   db_path = Path(os.environ.get("DB_PATH", "/app/data/registrations.db"))
   with tempfile.TemporaryDirectory() as tmp:
       copy = Path(tmp) / "registrations.db"
       with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(copy)) as dest:
           src.backup(dest)
           if dest.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
               raise SystemExit("Проверка резервной копии SQLite не прошла")
       with copy.open("rb") as file:
           shutil.copyfileobj(file, sys.stdout.buffer)
   PY

   test -s "$backup_dir/registrations.db" && ls -lh "$backup_dir"
   ```

   Продолжайте после успешной проверки. Метод SQLite `backup()` делает согласованную копию при работающем приложении. [Документация SQLite в Python](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)

4. Соберите образ из распакованного релиза, пока старая версия обслуживает посетителей.

   ```bash
   release_dir=$(mktemp -d)
   tar -xzf "$HOME/lis-site-release.tar.gz" -C "$release_dir" &&
     sudo docker build -t "lis-site:release-$stamp" "$release_dir"
   ```

5. После успешной сборки распакуйте релиз поверх кода в **текущей папке сайта** и обновите контейнер приложения. Caddy продолжит использовать действующие настройки HTTPS.

   ```bash
   tar -xzf "$HOME/lis-site-release.tar.gz" -C "$PWD" &&
     sudo docker image tag "lis-site:release-$stamp" lis-site:latest &&
     sudo docker compose up -d --no-deps --no-build --pull never app
   ```

   Compose пересоздаёт контейнер приложения с новым образом и сохраняет подключённые тома. [Документация Docker Compose](https://docs.docker.com/reference/cli/docker/compose/up/)

6. Проверьте состояние контейнеров, HTTPS, главную и вход в админку. Подставьте свой домен.

   ```bash
   sudo docker compose ps
   sudo docker compose logs --tail=60 app
   curl -fsS https://ВАШ-ДОМЕН/healthz
   curl -fsS https://ВАШ-ДОМЕН/ | grep -F 'Битва подразделений'
   ```

   В браузере откройте сайт и `/admin`. Проверьте, что в форме есть данные капитана и выбор размера команды. При первом запуске приложение создаёт таблицы команд и состава, а старые индивидуальные заявки хранит в исходной таблице. Если регистрация была закрыта, откройте её в админке, когда будете готовы принимать заявки.

### Откат Docker

В действующей папке сайта укажите папку резервной копии из шага 3 и верните прежние файлы и образ.

```bash
backup_dir="$HOME/lis-site-backups/УКАЖИТЕ_ДАТУ_И_ВРЕМЯ"
old_tag=$(cat "$backup_dir/image-tag")
tar -xzf "$backup_dir/site-files.tar.gz" -C "$PWD" &&
  sudo docker image tag "$old_tag" lis-site:latest &&
  sudo docker compose up -d --no-deps --no-build --pull never app
```

Откат кода использует текущую базу. Командные заявки, которые успели поступить, сохранятся в ней до повторного обновления.

## Gunicorn через systemd и nginx

Для установки из репозитория с каталогом `/opt/trash-forest` и сервисом `trash-forest` сначала подтвердите схему командой `sudo systemctl status trash-forest`. Сохраните код и согласованную копию SQLite, затем распакуйте релиз в действующий каталог и перезапустите сервис. Используйте фактический путь к SQLite, если у вас задан `DB_PATH`.

```bash
umask 077
backup_dir="$HOME/lis-site-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
sudo tar -czf - -C /opt/trash-forest app.py requirements.txt templates static \
  > "$backup_dir/site-files.tar.gz"
sudo /opt/trash-forest/.venv/bin/python - "$backup_dir/registrations.db" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect("file:/opt/trash-forest/data/registrations.db?mode=ro", uri=True)) as src, \
     closing(sqlite3.connect(sys.argv[1])) as dest:
    src.backup(dest)
    if dest.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise SystemExit("Проверка резервной копии SQLite не прошла")
PY
```

После успешного бэкапа выполните обновление. Список зависимостей в этом релизе совпадает с предыдущей версией репозитория.

```bash
sudo tar --no-same-owner -xzf "$HOME/lis-site-release.tar.gz" -C /opt/trash-forest &&
  sudo systemctl restart trash-forest
sudo systemctl status trash-forest
curl -fsS https://ВАШ-ДОМЕН/healthz
```

Для отката распакуйте `site-files.tar.gz` из папки резервной копии вместо архива релиза и перезапустите `trash-forest`.
