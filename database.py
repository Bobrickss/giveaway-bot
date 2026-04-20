import sqlite3
import uuid
from datetime import datetime
from typing import Optional


class Database:
    def __init__(self, path: str = "giveaways.db"):
        self.path = path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    id TEXT PRIMARY KEY,
                    creator_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    winners_count INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    secret_winner_id INTEGER,
                    post_chat_id INTEGER,
                    post_message_id INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    UNIQUE(giveaway_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS winners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    chosen_at TEXT NOT NULL
                );
            """)

    def create_giveaway(self, creator_id: int, title: str, description: str, winners_count: int) -> str:
        gid = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO giveaways (id, creator_id, title, description, winners_count, created_at) VALUES (?,?,?,?,?,?)",
                (gid, creator_id, title, description, winners_count, datetime.now().isoformat())
            )
        return gid

    def get_giveaway(self, giveaway_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()
            return dict(row) if row else None

    def get_user_giveaways(self, user_id: int) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM giveaways WHERE creator_id=? AND status='active' ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def add_participant(self, giveaway_id: str, user_id: int, username: str) -> bool:
        """Returns True if already participating."""
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO participants (giveaway_id, user_id, username, joined_at) VALUES (?,?,?,?)",
                    (giveaway_id, user_id, username, datetime.now().isoformat())
                )
            return False
        except sqlite3.IntegrityError:
            return True

    def get_participants(self, giveaway_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM participants WHERE giveaway_id=? ORDER BY joined_at ASC",
                (giveaway_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_participant_count(self, giveaway_id: str) -> int:
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM participants WHERE giveaway_id=?", (giveaway_id,)
            ).fetchone()[0]

    def get_participant_by_id(self, giveaway_id: str, user_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM participants WHERE giveaway_id=? AND user_id=?",
                (giveaway_id, user_id)
            ).fetchone()
            return dict(row) if row else None

    def set_secret_winner(self, giveaway_id: str, user_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE giveaways SET secret_winner_id=? WHERE id=?",
                (user_id, giveaway_id)
            )

    def get_secret_winner(self, giveaway_id: str) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT secret_winner_id FROM giveaways WHERE id=?", (giveaway_id,)
            ).fetchone()
            return row["secret_winner_id"] if row else None

    def update_giveaway_message(self, giveaway_id: str, chat_id: int, message_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE giveaways SET post_chat_id=?, post_message_id=? WHERE id=?",
                (chat_id, message_id, giveaway_id)
            )

    def get_giveaway_post(self, giveaway_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT post_chat_id, post_message_id FROM giveaways WHERE id=?", (giveaway_id,)
            ).fetchone()
            if row and row["post_chat_id"]:
                return {"chat_id": row["post_chat_id"], "message_id": row["post_message_id"]}
            return None

    def finish_giveaway(self, giveaway_id: str, winner_ids: list):
        with self._conn() as conn:
            conn.execute("UPDATE giveaways SET status='finished' WHERE id=?", (giveaway_id,))
            for uid in winner_ids:
                conn.execute(
                    "INSERT INTO winners (giveaway_id, user_id, chosen_at) VALUES (?,?,?)",
                    (giveaway_id, uid, datetime.now().isoformat())
                )

    def cancel_giveaway(self, giveaway_id: str):
        with self._conn() as conn:
            conn.execute("UPDATE giveaways SET status='cancelled' WHERE id=?", (giveaway_id,))


db = Database()
