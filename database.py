"""
Database abstraction layer for PawChat.
Supports SQLite (default) and PostgreSQL (set DB_TYPE=postgresql and DATABASE_URL).
"""
import sqlite3
import os
from datetime import datetime


DATABASE_TYPE = os.environ.get("DB_TYPE", "sqlite")
DATABASE_URL = os.environ.get("DATABASE_URL", "chat.db")


class Database:
    def __init__(self):
        self.db_type = DATABASE_TYPE
        self.db_url = DATABASE_URL

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def get_connection(self):
        if self.db_type == "postgresql":
            try:
                import psycopg2
                import psycopg2.extras
                conn = psycopg2.connect(self.db_url)
                conn.cursor_factory = psycopg2.extras.RealDictCursor
                return conn
            except ImportError:
                raise RuntimeError(
                    "psycopg2 is not installed. Run: pip install psycopg2-binary"
                )
        else:
            conn = sqlite3.connect(self.db_url, timeout=15)
            conn.row_factory = sqlite3.Row
            # WAL mode allows concurrent reads alongside writes
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn

    def _ph(self):
        """Return the correct SQL placeholder for the active DB."""
        return "%s" if self.db_type == "postgresql" else "?"

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def init(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()

        if self.db_type == "postgresql":
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id         SERIAL PRIMARY KEY,
                    title      TEXT        NOT NULL DEFAULT 'New Conversation',
                    model      TEXT        NOT NULL DEFAULT 'llama3.2',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id              SERIAL PRIMARY KEY,
                    conversation_id INTEGER     NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    role            TEXT        NOT NULL,
                    content         TEXT        NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id)
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
                    title      TEXT     NOT NULL DEFAULT 'New Conversation',
                    model      TEXT     NOT NULL DEFAULT 'llama3.2',
                    created_at TEXT     NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT     NOT NULL DEFAULT (datetime('now'))
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    role            TEXT    NOT NULL,
                    content         TEXT    NOT NULL,
                    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id)
            """)

        conn.commit()

        # Migrations: add new columns / tables if they don't exist yet
        self._migrate(conn)

        conn.commit()
        conn.close()
        print(f"[DB] Initialised ({self.db_type}): {self.db_url}")

    def _migrate(self, conn):
        """Apply incremental schema migrations safely."""
        cursor = conn.cursor()
        if self.db_type == "postgresql":
            # Add system_prompt column
            cursor.execute("""
                ALTER TABLE conversations ADD COLUMN IF NOT EXISTS system_prompt TEXT
            """)
            cursor.execute("""
                ALTER TABLE conversations ADD COLUMN IF NOT EXISTS web_search_enabled INTEGER DEFAULT 0
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_files (
                    id              SERIAL PRIMARY KEY,
                    conversation_id INTEGER     NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    filename        TEXT        NOT NULL,
                    mimetype        TEXT        NOT NULL,
                    content         TEXT        NOT NULL,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
            """)
        else:
            # SQLite: ADD COLUMN IF NOT EXISTS not supported until 3.37, use try/except
            for col_sql in [
                "ALTER TABLE conversations ADD COLUMN system_prompt TEXT",
                "ALTER TABLE conversations ADD COLUMN web_search_enabled INTEGER DEFAULT 0",
            ]:
                try:
                    cursor.execute(col_sql)
                except Exception:
                    pass  # column already exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_files (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    filename        TEXT    NOT NULL,
                    mimetype        TEXT    NOT NULL,
                    content         TEXT    NOT NULL,
                    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
            """)
        conn.commit()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def get_conversations(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"SELECT * FROM conversations WHERE id = {ph}", (conv_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def create_conversation(self, title="New Conversation", model="llama3.2"):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        if self.db_type == "postgresql":
            cursor.execute(
                f"INSERT INTO conversations (title, model) VALUES ({ph}, {ph}) RETURNING id",
                (title, model),
            )
            conv_id = cursor.fetchone()["id"]
        else:
            cursor.execute(
                f"INSERT INTO conversations (title, model) VALUES ({ph}, {ph})",
                (title, model),
            )
            conv_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return conv_id

    def update_conversation(self, conv_id, title=None, model=None, system_prompt=None, web_search_enabled=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        now_expr = "NOW()" if self.db_type == "postgresql" else 'datetime("now")'
        if title is not None:
            cursor.execute(
                f"UPDATE conversations SET title = {ph}, updated_at = {now_expr} WHERE id = {ph}",
                (title, conv_id),
            )
        if model is not None:
            cursor.execute(
                f"UPDATE conversations SET model = {ph} WHERE id = {ph}",
                (model, conv_id),
            )
        if system_prompt is not None:
            cursor.execute(
                f"UPDATE conversations SET system_prompt = {ph} WHERE id = {ph}",
                (system_prompt, conv_id),
            )
        if web_search_enabled is not None:
            cursor.execute(
                f"UPDATE conversations SET web_search_enabled = {ph} WHERE id = {ph}",
                (1 if web_search_enabled else 0, conv_id),
            )
        conn.commit()
        conn.close()

    def delete_conversation(self, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"DELETE FROM conversations WHERE id = {ph}", (conv_id,)
        )
        conn.commit()
        conn.close()

    def touch_conversation(self, conv_id):
        """Update the updated_at timestamp."""
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        now_expr = "NOW()" if self.db_type == "postgresql" else "datetime('now')"
        cursor.execute(
            f"UPDATE conversations SET updated_at = {now_expr} WHERE id = {ph}",
            (conv_id,),
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def get_messages(self, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"SELECT * FROM messages WHERE conversation_id = {ph} ORDER BY created_at ASC",
            (conv_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_message(self, conv_id, role, content):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"INSERT INTO messages (conversation_id, role, content) VALUES ({ph}, {ph}, {ph})",
            (conv_id, role, content),
        )
        # Bump conversation timestamp
        now_expr = "NOW()" if self.db_type == "postgresql" else "datetime('now')"
        cursor.execute(
            f"UPDATE conversations SET updated_at = {now_expr} WHERE id = {ph}",
            (conv_id,),
        )
        conn.commit()
        conn.close()

    def message_count(self, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = {ph}",
            (conv_id,),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row)["cnt"]

    # ------------------------------------------------------------------
    # Conversation files
    # ------------------------------------------------------------------

    def get_conversation_files(self, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"SELECT id, conversation_id, filename, mimetype, created_at FROM conversation_files WHERE conversation_id = {ph} ORDER BY created_at ASC",
            (conv_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_conversation_file(self, file_id, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"SELECT * FROM conversation_files WHERE id = {ph} AND conversation_id = {ph}",
            (file_id, conv_id),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def add_conversation_file(self, conv_id, filename, mimetype, content):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        if self.db_type == "postgresql":
            cursor.execute(
                f"INSERT INTO conversation_files (conversation_id, filename, mimetype, content) VALUES ({ph},{ph},{ph},{ph}) RETURNING id",
                (conv_id, filename, mimetype, content),
            )
            file_id = cursor.fetchone()["id"]
        else:
            cursor.execute(
                f"INSERT INTO conversation_files (conversation_id, filename, mimetype, content) VALUES ({ph},{ph},{ph},{ph})",
                (conv_id, filename, mimetype, content),
            )
            file_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return file_id

    def delete_conversation_file(self, file_id, conv_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(
            f"DELETE FROM conversation_files WHERE id = {ph} AND conversation_id = {ph}",
            (file_id, conv_id),
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # App-wide settings (key/value store)
    # ------------------------------------------------------------------

    def get_setting(self, key, default=""):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        cursor.execute(f"SELECT value FROM settings WHERE key = {ph}", (key,))
        row = cursor.fetchone()
        conn.close()
        return dict(row)["value"] if row else default

    def set_setting(self, key, value):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        if self.db_type == "postgresql":
            cursor.execute(
                f"INSERT INTO settings (key, value) VALUES ({ph},{ph}) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )
        else:
            cursor.execute(
                f"INSERT OR REPLACE INTO settings (key, value) VALUES ({ph},{ph})",
                (key, value),
            )
        conn.commit()
        conn.close()

    def get_all_settings(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
