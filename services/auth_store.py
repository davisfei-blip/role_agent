import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional


class AuthStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "app_data.db"
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expert_registry (
                    config_key TEXT PRIMARY KEY,
                    owner_user_id INTEGER,
                    created_source TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(owner_user_id) REFERENCES users(id)
                )
                """
            )
            conn.commit()

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _normalize_username(self, username):
        return str(username or "").strip().lower()

    def user_count(self):
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            return int(row["count"] or 0)

    def _hash_password(self, password):
        salt = secrets.token_bytes(16)
        iterations = 200_000
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"

    def _verify_password(self, password, encoded_hash):
        try:
            algorithm, iteration_text, salt_hex, digest_hex = str(encoded_hash or "").split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iteration_text)
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                iterations,
            )
            return hmac.compare_digest(candidate.hex(), digest_hex)
        except Exception:
            return False

    def _row_to_user(self, row):
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_user_by_id(self, user_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, role, is_active, created_at, updated_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            return self._row_to_user(row)

    def get_user_by_username(self, username):
        normalized = self._normalize_username(username)
        if not normalized:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, role, is_active, created_at, updated_at FROM users WHERE username = ?",
                (normalized,),
            ).fetchone()
            return self._row_to_user(row)

    def register_user(self, username, password, admin_usernames=None):
        normalized = self._normalize_username(username)
        password = str(password or "")
        if not normalized:
            raise ValueError("用户名不能为空")
        if len(normalized) < 3:
            raise ValueError("用户名至少需要 3 个字符")
        if len(password) < 6:
            raise ValueError("密码至少需要 6 位")

        admin_usernames = {self._normalize_username(item) for item in (admin_usernames or []) if str(item or "").strip()}
        role = "admin" if normalized in admin_usernames else "user"

        now = self._now()
        encoded_hash = self._hash_password(password)
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (normalized, encoded_hash, role, now, now),
                )
                conn.commit()
                return self.get_user_by_id(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在") from exc

    def authenticate(self, username, password, admin_usernames=None):
        normalized = self._normalize_username(username)
        if not normalized:
            return None
        admin_usernames = {self._normalize_username(item) for item in (admin_usernames or []) if str(item or "").strip()}

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (normalized,),
            ).fetchone()
            if not row or not row["is_active"]:
                return None
            if not self._verify_password(password, row["password_hash"]):
                return None

            desired_role = "admin" if normalized in admin_usernames else row["role"]
            if desired_role != row["role"]:
                now = self._now()
                conn.execute(
                    "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                    (desired_role, now, row["id"]),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM users WHERE id = ?",
                    (row["id"],),
                ).fetchone()

        return self._row_to_user(row)

    def sync_experts(self, config_keys: Iterable[str]):
        now = self._now()
        normalized_keys = []
        for config_key in config_keys:
            key = str(config_key or "").strip()
            if key:
                normalized_keys.append(key)

        with self._connect() as conn:
            for config_key in normalized_keys:
                conn.execute(
                    """
                    INSERT INTO expert_registry (config_key, owner_user_id, created_source, created_at, updated_at)
                    VALUES (?, NULL, 'system', ?, ?)
                    ON CONFLICT(config_key) DO UPDATE SET updated_at = excluded.updated_at
                    """,
                    (config_key, now, now),
                )
            conn.commit()

    def assign_expert_owner(self, config_key, user_id, created_source="user"):
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO expert_registry (config_key, owner_user_id, created_source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(config_key) DO UPDATE SET
                    owner_user_id = excluded.owner_user_id,
                    created_source = excluded.created_source,
                    updated_at = excluded.updated_at
                """,
                (config_key, user_id, created_source, now, now),
            )
            conn.commit()

    def delete_expert(self, config_key):
        with self._connect() as conn:
            conn.execute("DELETE FROM expert_registry WHERE config_key = ?", (config_key,))
            conn.commit()

    def get_expert_record(self, config_key):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT e.config_key, e.owner_user_id, e.created_source, e.created_at, e.updated_at,
                       u.username AS owner_username, u.role AS owner_role
                FROM expert_registry e
                LEFT JOIN users u ON u.id = e.owner_user_id
                WHERE e.config_key = ?
                """,
                (config_key,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_expert_record(row)

    def list_expert_records(self, config_keys=None) -> Dict[str, dict]:
        query = """
            SELECT e.config_key, e.owner_user_id, e.created_source, e.created_at, e.updated_at,
                   u.username AS owner_username, u.role AS owner_role
            FROM expert_registry e
            LEFT JOIN users u ON u.id = e.owner_user_id
        """
        params = []
        if config_keys:
            keys = [str(item).strip() for item in config_keys if str(item or "").strip()]
            if keys:
                placeholders = ",".join("?" for _ in keys)
                query += f" WHERE e.config_key IN ({placeholders})"
                params.extend(keys)
        records = {}
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        for row in rows:
            records[row["config_key"]] = self._row_to_expert_record(row)
        return records

    def _row_to_expert_record(self, row):
        owner_username = row["owner_username"]
        return {
            "config_key": row["config_key"],
            "owner_user_id": row["owner_user_id"],
            "owner_username": owner_username,
            "owner_role": row["owner_role"],
            "owner_label": owner_username or "系统",
            "created_source": row["created_source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def can_edit_expert(self, user: Optional[dict], config_key: str) -> bool:
        if not user:
            return False
        if user.get("role") == "admin":
            return True
        record = self.get_expert_record(config_key)
        if not record:
            return False
        return record.get("owner_user_id") == user.get("id")
