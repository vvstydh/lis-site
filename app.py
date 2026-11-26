"""Сайт регистрации на «Битву активистов»."""

import hashlib
import hmac
import io
import ipaddress
import logging
import logging.handlers
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template, request,
    session, url_for,
)
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "registrations.db"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")    # полный доступ
VIEWER_PASSWORD = os.environ.get("VIEWER_PASSWORD", "")  # только просмотр списка
SECRET_KEY = os.environ.get("SECRET_KEY", "")
PRODUCTION = os.environ.get("COOKIE_SECURE", "0") == "1"  # на сервере с HTTPS

if ADMIN_PASSWORD and ADMIN_PASSWORD == VIEWER_PASSWORD:
    raise SystemExit("ADMIN_PASSWORD и VIEWER_PASSWORD должны отличаться")
if PRODUCTION:
    problems = []
    if len(SECRET_KEY) < 32:
        problems.append("SECRET_KEY короче 32 символов")
    if len(ADMIN_PASSWORD) < 12:
        problems.append("ADMIN_PASSWORD короче 12 символов")
    if VIEWER_PASSWORD and len(VIEWER_PASSWORD) < 12:
        problems.append("VIEWER_PASSWORD короче 12 символов")
    if problems:
        raise SystemExit("Небезопасные настройки в .env: " + "; ".join(problems))

# задержки и лимиты для формы регистрации
SUBMIT_COOLDOWN = 5          # сек между отправками с одного устройства
AFTER_SUCCESS_COOLDOWN = 30  # сек после успешной заявки с этого устройства
MIN_FILL_SECONDS = 3         # быстрее человек форму не заполнит — это бот

EVENT = {
    "title": "Битва активистов",
    "date": "6 декабря",
    "date_iso": "12-06",
    "place": "УЛК",
}

# организаторы: логотип в static/img, ссылка на группу ВК
ORGANIZERS = [
    {"name": "ЛИС — лаборатория инициатив студентов", "logo": "img/logo-lis.webp",
     "url": "https://vk.ru/lis_tusur", "handle": "lis_tusur"},
    {"name": "ППОС ТУСУР — 60 лет единства студентов", "logo": "img/logo-ppos.webp",
     "url": "https://vk.ru/ppos_tusur", "handle": "ppos_tusur"},
]

MAX_TEAM_SIZE = 6
TEAM_SIZES = tuple(str(size) for size in range(1, MAX_TEAM_SIZE + 1))
MEMBER_POSITIONS = range(2, MAX_TEAM_SIZE + 1)
READINESS = ("Да, капитан!", "Конечно готовы!", "Сэр, да, сэр")

# принимаем и старые ссылки vk.com, но сохраняем всё как vk.ru
VK_RE = re.compile(r"^(?:https?://)?(?:www\.|m\.)?vk\.(?:ru|com)/([A-Za-z0-9_.]{2,64})/?$")

app = Flask(__name__)
app.jinja_env.globals["event"] = EVENT
app.config.update(
    SECRET_KEY=SECRET_KEY or secrets.token_hex(32),
    # __Host- : cookie только для этого домена и только по HTTPS
    SESSION_COOKIE_NAME="__Host-session" if PRODUCTION else "session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),  # вход в админку живёт 12 часов
    MAX_CONTENT_LENGTH=32 * 1024,
    MAX_FORM_PARTS=50,
)
# ---------- логи ----------
# logs/access.log — каждый запрос одной строкой; logs/app.log — входы, лимиты, подозрительное.
# Файлы ротируются: по 5 МБ, хранится 5 штук. Если папка недоступна для записи — пишем только в консоль.
LOG_DIR = os.environ.get("LOG_DIR", os.path.join(BASE_DIR, "logs"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lis_site")
access_log = logging.getLogger("lis_site.access")
access_log.propagate = False


@app.url_defaults
def static_version(endpoint, values):
    """Добавляет ?v=<время изменения файла> к адресам статики: поменяли CSS/JS/картинку —
    браузер сразу скачает новую версию, а не возьмёт старую из кэша."""
    if endpoint == "static" and "filename" in values and "v" not in values:
        try:
            values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
        except OSError:
            pass


def setup_file_logs():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        for logger, name in ((log, "app.log"), (access_log, "access.log")):
            h = logging.handlers.RotatingFileHandler(
                os.path.join(LOG_DIR, name), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s") if logger is access_log else fmt)
            logger.addHandler(h)
    except OSError as e:
        access_log.addHandler(logging.StreamHandler())
        log.warning("не могу писать логи в %s (%s) — пишу только в консоль", LOG_DIR, e)


setup_file_logs()
if not SECRET_KEY:
    log.warning("SECRET_KEY не задан: сессии сбросятся при перезапуске")


# ---------- база данных ----------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Старые индивидуальные заявки остаются в таблице registrations.
    with closing(sqlite3.connect(DB_PATH)) as db, db:
        db.executescript(
            f"""
            PRAGMA journal_mode=WAL;
            BEGIN;
            CREATE TABLE IF NOT EXISTS teams (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name       TEXT NOT NULL,
                captain_name    TEXT NOT NULL,
                captain_vk      TEXT NOT NULL UNIQUE,
                captain_faculty TEXT NOT NULL,
                captain_group   TEXT NOT NULL,
                readiness       TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                attended        INTEGER NOT NULL DEFAULT 0 CHECK (attended IN (0, 1)),
                attended_at     TEXT
            );
            CREATE TABLE IF NOT EXISTS team_members (
                team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                position   INTEGER NOT NULL CHECK (typeof(position) = 'integer' AND position BETWEEN 2 AND {MAX_TEAM_SIZE}),
                full_name  TEXT NOT NULL,
                group_name TEXT NOT NULL,
                PRIMARY KEY (team_id, position)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('registration_open', '1');
            COMMIT;
            """
        )


def registration_open():
    row = get_db().execute("SELECT value FROM settings WHERE key='registration_open'").fetchone()
    return row is None or row["value"] == "1"


def read_teams():
    db = get_db()
    with db:
        db.execute("BEGIN")
        teams = {row["id"]: dict(row, members=[]) for row in db.execute("SELECT * FROM teams ORDER BY id")}
        for member in db.execute("SELECT * FROM team_members ORDER BY team_id, position"):
            teams[member["team_id"]]["members"].append(member)
    for team in teams.values():
        team["size"] = 1 + len(team["members"])
    return list(teams.values())


# ---------- защита ----------

class RateLimiter:
    """Скользящее окно в памяти процесса: не больше `limit` событий за `window` секунд на ключ.
    Потокобезопасно (gunicorn работает с потоками), устаревшие ключи периодически удаляются."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.hits, self.lock, self.swept = {}, threading.Lock(), time.monotonic()

    def hit(self, key):
        """Засчитать событие. Вернуть 0, если можно, иначе сколько секунд ждать."""
        now = time.monotonic()
        with self.lock:
            if now - self.swept > self.window:
                self.hits = {k: q for k, q in self.hits.items() if q and now - q[-1] < self.window}
                self.swept = now
            q = self.hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return int(self.window - (now - q[0])) + 1
            q.append(now)
            return 0


class Cooldown:
    """Пауза для ключа (устройства): до какого момента новые отправки запрещены."""

    def __init__(self):
        self.until, self.lock, self.swept = {}, threading.Lock(), time.monotonic()

    def left(self, key):
        with self.lock:
            return max(0, int(self.until.get(key, 0) - time.monotonic() + 0.999))

    def start(self, key, seconds):
        now = time.monotonic()
        with self.lock:
            if now - self.swept > 300:
                self.until = {k: t for k, t in self.until.items() if t > now}
                self.swept = now
            self.until[key] = max(self.until.get(key, 0), now + seconds)


# Лимиты по IP мягкие: в Wi-Fi университета сотни студентов выходят с одного адреса.
# От «прокликивания» с одного устройства защищает device_cooldown, а эти лимиты — от скриптов.
REGISTER_BURST = RateLimiter(20, 60)    # с одного IP: 20 отправок в минуту…
REGISTER_IP = RateLimiter(200, 600)     # …и 200 за 10 минут
LOGIN_IP = RateLimiter(10, 900)        # 10 попыток входа за 15 минут
device_cooldown = Cooldown()

# Откуда может прийти обратный прокси (nginx на том же сервере или Caddy в Docker-сети)
TRUSTED_PROXIES = [
    ipaddress.ip_network(n.strip())
    for n in os.environ.get("TRUSTED_PROXIES", "127.0.0.1/32,::1/128").split(",") if n.strip()
]


def from_trusted_proxy(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in TRUSTED_PROXIES)


def client_ip():
    # X-Real-IP выставляет прокси; верим ему только если запрос пришёл от самого прокси
    ip = request.remote_addr or "?"
    if from_trusted_proxy(ip):
        ip = request.headers.get("X-Real-IP", ip)
    return ip


def clean(value, limit=300):
    """Убрать управляющие и невидимые символы, схлопнуть пробелы."""
    value = unicodedata.normalize("NFC", value or "")[: limit * 2]
    value = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs"))
    return " ".join(value.split())


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


def csrf_ok():
    sent = request.form.get("csrf", "")
    return bool(sent) and hmac.compare_digest(sent, session.get("csrf", ""))


def check_csrf():
    if not csrf_ok():
        abort(400)


def role_fingerprint(role):
    """Отпечаток текущего пароля роли: сменили пароль в .env — старые входы перестают работать."""
    password = ADMIN_PASSWORD if role == "admin" else VIEWER_PASSWORD
    key = app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{role}:{password}".encode(), hashlib.sha256).hexdigest()[:24]


def current_role():
    role = session.get("role")
    if role in ("admin", "viewer") and hmac.compare_digest(session.get("pwv", ""), role_fingerprint(role)):
        return role
    return None


def login_required(view):
    """Любой вошедший: и админ, и наблюдатель."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_role() is None:
            session.pop("role", None)
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    """Только полный админ; наблюдателю — 403."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        role = current_role()
        if role is None:
            session.pop("role", None)
            return redirect(url_for("admin_login"))
        if role != "admin":
            log.warning("наблюдатель пытался изменить данные: %s %s", request.path, client_ip())
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def password_role(password):
    """Сверяем с обоими паролями всегда, чтобы время ответа не выдавало, какой из них подошёл."""
    is_admin = bool(ADMIN_PASSWORD) & hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
    is_viewer = bool(VIEWER_PASSWORD) & hmac.compare_digest(password.encode(), VIEWER_PASSWORD.encode())
    return "admin" if is_admin else "viewer" if is_viewer else None


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


@app.before_request
def start_timer():
    g.t0 = time.perf_counter()


def log_safe(value, limit):
    # переводы строк и кавычки экранируем — в лог нельзя подсунуть поддельную строку
    return value[:limit].replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace('"', '\\"')


@app.after_request
def log_request(resp):
    if request.path.startswith("/static/") or request.path == "/healthz":
        return resp
    ms = (time.perf_counter() - g.get("t0", time.perf_counter())) * 1000
    access_log.info('%s "%s %s" %s %s %.0fms "%s"',
                    client_ip(), request.method, log_safe(request.path, 300), resp.status_code,
                    resp.content_length if resp.content_length is not None else "-", ms,
                    log_safe(request.headers.get("User-Agent", "-"), 200))
    return resp


@app.after_request
def security_headers(resp):
    h = resp.headers
    h["Content-Security-Policy"] = CSP
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    if PRODUCTION:
        h["Strict-Transport-Security"] = "max-age=31536000"
    if not request.path.startswith("/static/"):
        # страницы с токенами и персональными данными не кешируем
        h["Cache-Control"] = "no-store"
    if request.path.startswith("/admin"):
        h["Referrer-Policy"] = "no-referrer"
        h["X-Robots-Tag"] = "noindex, nofollow"
    return resp


ERRORS = {  # код: (подзаголовок, текст)
    400: ("Что-то пошло не так", "Запрос не понят. Обновите страницу и попробуйте ещё раз."),
    403: ("Сюда нельзя", "У этого входа нет прав на такое действие."),
    404: ("Страница не найдена", "Проверьте адрес или вернитесь на главную."),
    405: ("Действие недоступно", "Вернитесь на главную и попробуйте снова."),
    413: ("Слишком тяжело", "Запрос слишком большой."),
    429: ("Притормози", "Слишком много запросов. Подожди немного и попробуй снова."),
    500: ("Ошибка на сайте", "Попробуйте открыть страницу через пару минут."),
}


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
@app.errorhandler(429)
@app.errorhandler(500)
def error_page(err):
    code = getattr(err, "code", 500) or 500
    kicker, message = ERRORS.get(code, ERRORS[500])
    return render_template("error.html", code=code, kicker=kicker, message=message), code


@app.get("/healthz")
def healthz():
    # для проверки здоровья контейнера; в логи не пишется
    get_db().execute("SELECT 1")
    return "ok"


@app.get("/favicon.ico")
def favicon():
    # браузеры просят значок по этому адресу сами, даже без <link rel="icon">
    return app.send_static_file("favicon.ico")


@app.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow: /admin\n", mimetype="text/plain")


# ---------- публичная часть ----------

def render_index(errors=None, form=None, status=200, retry_after=0):
    resp = app.make_response((render_template(
        "index.html",
        team_sizes=TEAM_SIZES,
        member_positions=MEMBER_POSITIONS,
        max_team_size=MAX_TEAM_SIZE,
        readiness=READINESS,
        organizers=ORGANIZERS,
        is_open=registration_open(),
        errors=errors or {},
        form=form or {},
        retry_after=retry_after,
    ), status))
    if retry_after:
        resp.headers["Retry-After"] = str(retry_after)
    return resp


def start_form():
    """Метки устройства и времени открытия формы — для паузы между отправками и ловушки на ботов."""
    session.setdefault("did", secrets.token_hex(12))
    session["form_at"] = time.time()


@app.get("/")
def index():
    start_form()
    return render_index(retry_after=device_cooldown.left(session["did"]))


@app.post("/register")
def register():
    limits = {
        "captain_name": 120, "captain_vk": 100, "captain_faculty": 120, "captain_group": 40,
        "team_name": 160, "team_size": 1, "readiness": 60,
    }
    for position in MEMBER_POSITIONS:
        limits[f"member_name_{position}"] = 120
        limits[f"member_group_{position}"] = 40
    form = {k: clean(request.form.get(k), n) for k, n in limits.items()}
    ip = client_ip()

    # 1. лимиты по IP — режем поток раньше всего остального
    wait = REGISTER_BURST.hit(ip) or REGISTER_IP.hit(ip)
    if wait:
        log.warning("лимит регистраций по IP %s", ip)
        return render_index({"_form": f"Слишком много заявок с этой сети. Попробуйте через {wait} с."}, form, 429, wait)

    # 2. CSRF + страница должна быть открыта на этом устройстве (есть метка устройства)
    did = session.get("did")
    if not csrf_ok() or not did:
        start_form()
        return render_index({"_form": "Страница устарела — проверьте данные и отправьте ещё раз."}, form, 400)

    # 3. пауза между отправками с одного устройства
    wait = device_cooldown.left(did)
    if wait:
        return render_index({"_form": f"Подождите {wait} с перед повторной отправкой."}, form, 429, wait)
    device_cooldown.start(did, SUBMIT_COOLDOWN)

    # 4. боты: заполненное скрытое поле или форма отправлена мгновенно
    if request.form.get("website"):
        log.info("honeypot сработал: %s", ip)
        return redirect(url_for("success"))
    if time.time() - session.get("form_at", 0) < MIN_FILL_SECONDS:
        session["form_at"] = time.time()
        return render_index({"_form": "Слишком быстро. Проверьте данные и отправьте ещё раз."}, form, 400, SUBMIT_COOLDOWN)

    errors = {}
    name_error = "Укажите фамилию и имя (можно с отчеством)."
    group_error = "Укажите номер группы (до 40 символов)."
    if len(form["captain_name"].split()) < 2 or len(form["captain_name"]) > 120:
        errors["captain_name"] = name_error
    vk = VK_RE.fullmatch(form["captain_vk"])
    if not vk:
        errors["captain_vk"] = "Нужна ссылка вида vk.ru/username."
    if not 1 <= len(form["captain_faculty"]) <= 120:
        errors["captain_faculty"] = "Укажите факультет капитана."
    if not 1 <= len(form["captain_group"]) <= 40:
        errors["captain_group"] = group_error
    if not 1 <= len(form["team_name"]) <= 160:
        errors["team_name"] = "Укажите название команды (до 160 символов)."
    team_size = int(form["team_size"]) if form["team_size"] in TEAM_SIZES else None
    members = []
    if team_size is None:
        errors["team_size"] = f"Выберите от 1 до {MAX_TEAM_SIZE} участников, включая капитана."
    else:
        for position in MEMBER_POSITIONS:
            name_field, group_field = f"member_name_{position}", f"member_group_{position}"
            name, group = form[name_field], form[group_field]
            if position > team_size:
                if name or group:
                    errors["team_size"] = "Проверьте количество участников: указаны данные лишнего участника."
                continue
            if len(name.split()) < 2 or len(name) > 120:
                errors[name_field] = name_error
            if not 1 <= len(group) <= 40:
                errors[group_field] = group_error
            members.append((position, name, group))
    if form["readiness"] not in READINESS:
        errors["readiness"] = "Выберите вариант."
    if errors:
        return render_index(errors, form, 400, SUBMIT_COOLDOWN)

    vk_link = f"https://vk.ru/{vk.group(1).lower()}"
    result = save_team(form, vk_link, members)
    if result == "closed":
        return render_index({"_form": "Увы, регистрация уже закрыта."}, form, 409)
    if result == "duplicate":
        return render_index(
            {"captain_vk": "С этой страницей ВК уже зарегистрирована команда."}, form, 409, SUBMIT_COOLDOWN,
        )

    device_cooldown.start(did, AFTER_SUCCESS_COOLDOWN)
    session["form_at"] = time.time()
    return redirect(url_for("success"))


def save_team(form, vk_link, members):
    db = get_db()
    with db:
        # Проверка открытия регистрации и запись всей команды выполняются под одной блокировкой.
        db.execute("BEGIN IMMEDIATE")
        if not registration_open():
            return "closed"
        cursor = db.execute(
            "INSERT INTO teams (team_name, captain_name, captain_vk, captain_faculty, captain_group, readiness, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(captain_vk) DO NOTHING",
            (form["team_name"], form["captain_name"], vk_link, form["captain_faculty"], form["captain_group"],
             form["readiness"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        if cursor.rowcount == 0:
            return "duplicate"
        db.executemany(
            "INSERT INTO team_members (team_id, position, full_name, group_name) VALUES (?, ?, ?, ?)",
            ((cursor.lastrowid, *member) for member in members),
        )
    return "saved"


@app.get("/success")
def success():
    return render_template("success.html")


# ---------- админка ----------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        check_csrf()
        ip = client_ip()
        if wait := LOGIN_IP.hit(ip):
            log.warning("лимит входов в админку: %s", ip)
            error = f"Слишком много попыток. Подождите {wait // 60 + 1} мин."
            return render_template("login.html", error=error), 429, {"Retry-After": str(wait)}
        if role := password_role(request.form.get("password", "")[:200]):
            session.clear()  # новая сессия при входе — защита от фиксации сессии
            session.permanent = True
            session["role"], session["pwv"] = role, role_fingerprint(role)
            log.info("вход в админку: %s (%s)", role, ip)
            return redirect(url_for("admin"))
        log.warning("неверный пароль админки: %s", ip)
        error = "Неверный пароль."
    return render_template("login.html", error=error)


@app.post("/admin/logout")
def admin_logout():
    check_csrf()
    session.clear()
    return redirect(url_for("index"))


@app.get("/admin")
@login_required
def admin():
    teams = read_teams()
    return render_template(
        "admin.html", teams=teams, came=sum(team["attended"] for team in teams),
        participants=sum(team["size"] for team in teams), is_open=registration_open(),
        can_edit=current_role() == "admin",
    )


@app.post("/admin/toggle")
@admin_required
def admin_toggle():
    check_csrf()
    row = get_db().execute(
        "UPDATE settings SET value=CASE value WHEN '1' THEN '0' ELSE '1' END"
        " WHERE key='registration_open' RETURNING value"
    ).fetchone()
    flash("Регистрация открыта." if row["value"] == "1" else "Регистрация закрыта.")
    return redirect(url_for("admin"))


@app.post("/admin/teams/<int:team_id>/delete")
@admin_required
def admin_delete(team_id):
    check_csrf()
    deleted = get_db().execute("DELETE FROM teams WHERE id=?", (team_id,))
    if not deleted.rowcount:
        abort(404)
    flash("Заявка команды удалена.")
    return redirect(url_for("admin"))


@app.post("/admin/teams/<int:team_id>/attend")
@admin_required
def admin_attend(team_id):
    check_csrf()
    attended = request.form.get("attended") == "1"
    db = get_db()
    row = db.execute(
        "UPDATE teams SET attended=?,"
        " attended_at=CASE WHEN ? THEN COALESCE(attended_at, ?) ELSE NULL END"
        " WHERE id=? RETURNING attended_at",
        (attended, attended, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), team_id),
    ).fetchone()
    if row is None:
        abort(404)
    if request.headers.get("X-Requested-With") == "fetch":
        counts = db.execute("SELECT COUNT(*) AS total, COALESCE(SUM(attended), 0) AS came FROM teams").fetchone()
        return {"ok": True, "attended": attended, "attended_at": row["attended_at"], **dict(counts)}
    return redirect(url_for("admin"))


@app.get("/admin/export.xlsx")
@admin_required
def admin_export():
    data = build_xlsx(read_teams())
    name = f"команды_{datetime.now():%Y-%m-%d}.xlsx"
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=teams.xlsx; filename*=UTF-8''{quote(name)}"},
    )


def as_text(cell):
    """Защита от Excel-инъекции: то, что ввёл участник, всегда текст, даже если начинается с «=»."""
    if isinstance(cell.value, str):
        cell.data_type = "s"
    return cell


def write_sheet(ws, columns, rows):
    header_fill = PatternFill("solid", fgColor="25496E")
    ws.append([label for label, _ in columns])
    for number, (_, width) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=number)
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(number)].width = width
    ws.row_dimensions[1].height = 30

    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(vertical="top", wrap_text=True)
    for values in rows:
        ws.append(values)
        for cell in ws[ws.max_row]:
            cell.border, cell.alignment = border, wrap
            as_text(cell)
            if isinstance(cell.value, datetime):
                cell.number_format = "DD.MM.YYYY HH:MM"

    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions


def build_xlsx(teams):
    wb = Workbook()
    ws = wb.active
    ws.title = "Команды"

    def as_datetime(value):
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else None

    write_sheet(
        ws,
        [("№", 7), ("Команда", 34), ("Участников", 14), ("ФИО капитана", 35),
         ("ВК капитана", 31), ("Факультет капитана", 25), ("Группа капитана", 18),
         ("Вы готовы дети?", 22), ("Пришла", 12), ("Время отметки", 21), ("Дата заявки", 21)],
        ([team["id"], team["team_name"], team["size"], team["captain_name"], team["captain_vk"],
          team["captain_faculty"], team["captain_group"], team["readiness"], "Да" if team["attended"] else "Нет",
          as_datetime(team["attended_at"]), as_datetime(team["created_at"])] for team in teams),
    )
    for number, team in enumerate(teams, 2):
        link = ws.cell(row=number, column=5)
        link.hyperlink = team["captain_vk"]
        link.font = Font(color="0563C1", underline="single")
    if teams:
        green = PatternFill("solid", fgColor="C6EFCE")
        ws.conditional_formatting.add(f"I2:I{ws.max_row}", CellIsRule(operator="equal", formula=['"Да"'], fill=green))

    roster = []
    for team in teams:
        prefix = (team["id"], team["team_name"])
        roster.append((*prefix, 1, "Капитан", team["captain_name"], team["captain_group"]))
        roster.extend(
            (*prefix, member["position"], "Участник", member["full_name"], member["group_name"])
            for member in team["members"]
        )
    write_sheet(
        wb.create_sheet("Состав команд"),
        [("№ команды", 13), ("Команда", 34), ("№ в составе", 14), ("Роль", 14), ("ФИО", 38), ("Группа", 19)],
        roster,
    )

    summary = wb.create_sheet("Сводка")
    summary.column_dimensions["A"].width = 38
    summary.column_dimensions["B"].width = 16
    summary["A1"] = f"{EVENT['title']} — {EVENT['date']}, {EVENT['place']}"
    summary["A1"].font = Font(bold=True, size=14)
    last = max(ws.max_row, 2)
    totals = [
        ("Зарегистрировалось команд", f"=COUNTA('Команды'!A2:A{last})"),
        ("Участников, включая капитанов", f"=SUM('Команды'!C2:C{last})"),
        ("Пришло команд", f"""=COUNTIF('Команды'!I2:I{last},"Да")"""),
        ("Команд ещё нет", "=B3-B5"),
        ("Явка команд", "=IF(B3=0,0,B5/B3)"),
    ]
    for number, (label, formula) in enumerate(totals, 3):
        summary.cell(row=number, column=1, value=label).font = Font(bold=True)
        summary.cell(row=number, column=2, value=formula)
    summary["B7"].number_format = "0%"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


init_db()

if __name__ == "__main__":
    # локальный запуск; отладчик включается только явно: FLASK_DEBUG=1
    app.run(host="127.0.0.1", port=5050, debug=os.environ.get("FLASK_DEBUG") == "1")
