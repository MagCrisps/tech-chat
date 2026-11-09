"""用户与会话存储：SQLite + bcrypt，附带命令行用户管理。

设计要点：
- **密码只存 bcrypt 哈希**，不可逆；验证用 `bcrypt.checkpw`。
- **会话只存 token 的 SHA-256**，数据库泄露也拿不到可用的 cookie。
- **不提供注册接口**，只有持有服务器访问权限的人才能用命令行加用户：
      python -m app.users add <用户名>     # 交互式输入密码
      python -m app.users list
      python -m app.users passwd <用户名>
      python -m app.users disable|enable <用户名>
      python -m app.users delete <用户名>
- **登录失败限流**：同一用户名 15 分钟内失败 LOGIN_MAX_FAILURES 次后，锁 15 分钟。
"""

from __future__ import annotations

import hashlib
import hmac
import getpass
import logging
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bcrypt

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "paperscout_session"
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS login_failures (
    username TEXT NOT NULL,
    ts       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failures ON login_failures(username, ts);
"""


@dataclass
class User:
    username: str
    created_at: int
    disabled: bool


def _db_path() -> Path:
    return settings.users_db_path


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    # bcrypt 只处理前 72 字节；超长密码截断而不是报错，避免用户困惑
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 用户管理（命令行使用）
# --------------------------------------------------------------------------- #
def add_user(username: str, password: str, *, enabled: bool = True) -> None:
    username = username.strip()
    if not username:
        raise ValueError("用户名不能为空")
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at, disabled) VALUES (?, ?, ?, ?)",
                (username, _hash_password(password), int(time.time()), 0 if enabled else 1),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"用户已存在：{username}") from exc


def set_password(username: str, password: str) -> None:
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (_hash_password(password), username))
        if cur.rowcount == 0:
            raise ValueError(f"用户不存在：{username}")
    delete_user_sessions(username)  # 改密码后让旧会话全部失效


def set_disabled(username: str, disabled: bool) -> None:
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET disabled = ? WHERE username = ?", (1 if disabled else 0, username))
        if cur.rowcount == 0:
            raise ValueError(f"用户不存在：{username}")
    if disabled:
        delete_user_sessions(username)


def delete_user(username: str) -> None:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        if cur.rowcount == 0:
            raise ValueError(f"用户不存在：{username}")
    delete_user_sessions(username)


def list_users() -> list[User]:
    with _connect() as conn:
        rows = conn.execute("SELECT username, created_at, disabled FROM users ORDER BY username").fetchall()
    return [User(r["username"], r["created_at"], bool(r["disabled"])) for r in rows]


def has_any_user() -> bool:
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


# --------------------------------------------------------------------------- #
# 登录与限流
# --------------------------------------------------------------------------- #
def _recent_failures(conn: sqlite3.Connection, username: str, now: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM login_failures WHERE username = ? AND ts > ?",
        (username, now - LOGIN_WINDOW_SECONDS),
    ).fetchone()
    return int(row["n"])


def _locked_until(conn: sqlite3.Connection, username: str, now: int) -> int:
    """返回锁定到期时间戳；未锁定返回 0。"""
    rows = conn.execute(
        "SELECT ts FROM login_failures WHERE username = ? AND ts > ? ORDER BY ts DESC",
        (username, now - LOCKOUT_SECONDS),
    ).fetchall()
    if len(rows) < LOGIN_MAX_FAILURES:
        return 0
    # 从第 LOGIN_MAX_FAILURES 次失败起算锁定时间
    return int(rows[LOGIN_MAX_FAILURES - 1]["ts"]) + LOCKOUT_SECONDS


def verify_login(username: str, password: str) -> tuple[bool, str | None]:
    """校验用户名密码。返回 (成功, 失败原因)。失败原因用于给用户的提示。"""
    username = username.strip()
    now = int(time.time())
    with _connect() as conn:
        locked_until = _locked_until(conn, username, now)
        if locked_until > now:
            return False, f"尝试次数过多，请 {max(1, (locked_until - now) // 60)} 分钟后再试"

        row = conn.execute("SELECT password_hash, disabled FROM users WHERE username = ?", (username,)).fetchone()
        ok = False
        if row is not None:
            try:
                ok = bcrypt.checkpw(password.encode("utf-8")[:72], row["password_hash"].encode())
            except ValueError:
                ok = False
            if ok and row["disabled"]:
                return False, "该账号已被停用"

        if ok:
            conn.execute("DELETE FROM login_failures WHERE username = ?", (username,))
            return True, None

        conn.execute("INSERT INTO login_failures (username, ts) VALUES (?, ?)", (username, now))
        # 用户名不存在与密码错误返回同一提示，避免被用来探测账号是否存在
        return False, "用户名或密码错误"


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
def create_session(username: str) -> tuple[str, int]:
    """创建会话，返回 (明文 token, 过期时间戳)。明文只在这一刻存在。"""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires_at = now + settings.session_ttl_days * 86400
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))  # 顺手清理过期会话
        conn.execute(
            "INSERT INTO sessions (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), username, now, expires_at),
        )
    return token, expires_at


def get_session_user(token: str | None) -> Optional[str]:
    """根据 token 返回用户名；无效或过期返回 None。"""
    if not token:
        return None
    now = int(time.time())
    with _connect() as conn:
        row = conn.execute(
            "SELECT s.username, u.disabled FROM sessions s JOIN users u ON u.username = s.username "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_token_hash(token), now),
        ).fetchone()
    if row is None or row["disabled"]:
        return None
    return str(row["username"])


def delete_session(token: str | None) -> None:
    if not token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def delete_user_sessions(username: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))


# --------------------------------------------------------------------------- #
# 命令行入口
# --------------------------------------------------------------------------- #
USAGE = """用法：python -m app.users <命令> [用户名]

  add <用户名>        新增用户（交互式输入密码，两次确认）
  passwd <用户名>     修改密码（同时使该用户所有会话失效）
  list                列出全部用户
  disable <用户名>    停用账号（立即踢下线）
  enable <用户名>     重新启用
  delete <用户名>     删除账号

说明：本系统不提供注册接口，只有能在服务器上执行本命令的人才能添加用户。
"""


def _prompt_password() -> str:
    first = getpass.getpass("密码（输入时不显示）：")
    second = getpass.getpass("再输一次：")
    if first != second:
        raise SystemExit("两次输入不一致")
    if len(first) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    return first


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0
    command, rest = argv[1], argv[2:]

    try:
        if command == "add":
            if not rest:
                raise SystemExit("缺少用户名")
            add_user(rest[0], _prompt_password())
            print(f"已添加用户：{rest[0]}")
        elif command == "passwd":
            if not rest:
                raise SystemExit("缺少用户名")
            set_password(rest[0], _prompt_password())
            print(f"已修改密码：{rest[0]}（该用户的旧会话已失效）")
        elif command == "list":
            users = list_users()
            if not users:
                print("还没有任何用户。用 `python -m app.users add <用户名>` 添加。")
                return 0
            print(f"{'用户名':<20}{'创建时间':<22}{'状态'}")
            for user in users:
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(user.created_at))
                print(f"{user.username:<20}{stamp:<22}{'已停用' if user.disabled else '正常'}")
        elif command in {"disable", "enable", "delete"}:
            if not rest:
                raise SystemExit("缺少用户名")
            if command == "delete":
                delete_user(rest[0])
                print(f"已删除用户：{rest[0]}")
            else:
                set_disabled(rest[0], command == "disable")
                print(f"已{'停用' if command == 'disable' else '启用'}用户：{rest[0]}")
        else:
            print(USAGE)
            return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
