"""Локальные пользователи и серверные сессии. Пароли сохраняются только как scrypt-хеши."""
import hashlib
import hmac
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request

from . import config, db

COOKIE = "inspector_session"


def _password(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


USERS = [("engineer", "Инженер", "engineer"), ("master", "Мастер", "master"), ("admin", "Администратор", "admin")]
ACCOUNTS_FILE = "demo-accounts.txt"


def _encode(password: str) -> str:
    salt = secrets.token_hex(16)
    return salt + ":" + _password(password, salt)


def env_password(username: str) -> str | None:
    """Пароль из .env (DEMO_PASSWORD_ENGINEER и т. п.) — чтобы на защите не набирать случайную строку."""
    value = os.getenv(f"DEMO_PASSWORD_{username.upper()}", "").strip()
    return value or None


def bootstrap() -> None:
    """Создаёт демо-учётки при первом запуске; пароли из .env применяются при каждом запуске."""
    with db.tx() as c:
        if not c.execute("SELECT id FROM users LIMIT 1").fetchone():
            credentials = []
            for username, display, role in USERS:
                password = env_password(username) or secrets.token_urlsafe(10)
                c.execute("INSERT INTO users(username,display_name,role,password_hash) VALUES(?,?,?,?)",
                          (username, display, role, _encode(password)))
                credentials.append(f"{username}: " + ("задан в .env" if env_password(username) else password))
            path = config.DATA_DIR / ACCOUNTS_FILE
            path.write_text("Локальные учетные записи демо-стенда\n" + "\n".join(credentials) + "\n", encoding="utf-8")
            path.chmod(0o600)
            return
        for username, _, _ in USERS:
            password = env_password(username)
            if password:
                c.execute("UPDATE users SET password_hash=? WHERE username=?", (_encode(password), username))


def accounts_hint() -> list[str]:
    """Строки «логин: пароль» для страницы /setup на самом сервере."""
    path = config.DATA_DIR / ACCOUNTS_FILE
    saved = dict(line.split(": ", 1) for line in path.read_text(encoding="utf-8").splitlines()[1:]
                 if ": " in line) if path.exists() else {}
    return [f"{u}: " + ("задан в .env" if env_password(u) else saved.get(u, "см. data/" + ACCOUNTS_FILE))
            for u, _, _ in USERS]


class LoginLimiter:
    """Не больше MAX неудачных входов за WINDOW секунд с одного адреса на одну учётку.
    В закрытой сети это защита от перебора с телефона, оставленного без присмотра, а не от бот-сети."""
    MAX, WINDOW = 5, 300

    def __init__(self) -> None:
        self._fails: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        return [t for t in self._fails.get(key, []) if now - t < self.WINDOW]

    def check(self, ip: str, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            recent = self._recent((ip, username), now)
            self._fails[(ip, username)] = recent
            if len(recent) >= self.MAX:
                wait = int(self.WINDOW - (now - recent[0])) + 1
                raise HTTPException(429, f"Слишком много неудачных попыток. Повторите через {wait} с.")

    def failed(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.setdefault((ip, username), []).append(time.monotonic())

    def succeeded(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop((ip, username), None)


limiter = LoginLimiter()


def login(username: str, password: str) -> tuple[str, dict]:
    row = db.query_one("SELECT * FROM users WHERE username=?", (username,))
    salt, expected = row["password_hash"].split(":") if row else ("00" * 16, "00" * 64)
    actual = _password(password, salt)
    if not row or not hmac.compare_digest(actual, expected):
        raise HTTPException(401, "Неверное имя пользователя или пароль.")
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        c.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + 12 * 3600))
    return token, {k: row[k] for k in ("id", "username", "display_name", "role")}


def current(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    return db.query_one("""SELECT u.id,u.username,u.display_name,u.role FROM users u
        JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?""",
        (hashlib.sha256(token.encode()).hexdigest(), time.time()))


def require(request: Request, roles: set[str] | None = None) -> dict:
    user = current(request)
    if not user:
        raise HTTPException(401, "Войдите в приложение.")
    if roles and user["role"] not in roles:
        raise HTTPException(403, "Для этого действия нужна другая роль.")
    return user


def role(*roles: str):
    """Зависимость FastAPI: `user: dict = Depends(auth.role("master", "admin"))`.
    Без аргументов — любой вошедший пользователь. Синхронная — FastAPI выполнит её в пуле потоков."""
    allowed = set(roles) or None

    def dependency(request: Request) -> dict:
        return require(request, allowed)
    return dependency


def logout(request: Request) -> None:
    token = request.cookies.get(COOKIE, "")
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
