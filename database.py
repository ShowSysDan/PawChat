"""
Database abstraction layer for Ollama Chat.
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
            conn = sqlite3.connect(self.db_url)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent read performance
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
        conn.close()
        print(f"[DB] Initialised ({self.db_type}): {self.db_url}")

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

    def update_conversation(self, conv_id, title=None, model=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        ph = self._ph()
        if title is not None:
            cursor.execute(
                f"UPDATE conversations SET title = {ph}, updated_at = {'NOW()' if self.db_type == 'postgresql' else 'datetime(\"now\")'} WHERE id = {ph}",
                (title, conv_id),
            )
        if model is not None:
            cursor.execute(
                f"UPDATE conversations SET model = {ph} WHERE id = {ph}",
                (model, conv_id),
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
