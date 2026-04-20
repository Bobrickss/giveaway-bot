import sqlite3
import json
import uuid
from datetime import datetime
from typing import Optional


class Database:
    def __init__(self, path="giveaways.db"):
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
                    photo_id TEXT DEFAULT '',
                    button_label TEXT DEFAULT 'Участвовать',
                    button_color TEXT DEFAULT '🔵 Синий',
                    tg_channels TEXT DEFAULT '[]',
                    ig_username TEXT DEFAULT '',
                    end_time TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    secret_winner_id INTEGER,
                    post_chat_id TEXT,
                    post_message_id INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    profile_link TEXT DEFAULT '',
                    joined_at TEXT NOT NULL,
                    UNIQUE(giveaway_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS winners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    giveaway_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    chosen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS captcha_sessions (
                    user_id INTEGER NOT NULL,
                    giveaway_id TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, giveaway_id)
                );
                CREATE TABLE IF NOT EXISTS pending_publish (
                    user_id INTEGER PRIMARY KEY,
                    giveaway_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    def create_giveaway(self, creator_id, title, description, winners_count,
                        photo_id="", button_label="Участвовать", button_color="🔵 Синий",
                        tg_channels=None, ig_username="", end_time=""):
        gid = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO giveaways (id,creator_id,title,description,winners_count,photo_id,button_label,button_color,tg_channels,ig_username,end_time,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (gid, creator_id, title, description, winners_count, photo_id, button_label, button_color,
                 json.dumps(tg_channels or [], ensure_ascii=False), ig_username, end_time, datetime.now().isoformat())
            )
        return gid

    def get_giveaway(self, gid):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()
            if not row: return None
            d = dict(row)
            try: d["tg_channels"] = json.loads(d.get("tg_channels") or "[]")
            except: d["tg_channels"] = []
            return d

    def get_user_giveaways(self, user_id):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM giveaways WHERE creator_id=? AND status='active' ORDER BY created_at DESC",
                (user_id,)
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try: d["tg_channels"] = json.loads(d.get("tg_channels") or "[]")
                except: d["tg_channels"] = []
                result.append(d)
            return result

    def is_participant(self, gid, user_id):
        with self._conn() as conn:
            return conn.execute(
                "SELECT 1 FROM participants WHERE giveaway_id=? AND user_id=?", (gid, user_id)
            ).fetchone() is not None

    def add_participant(self, gid, user_id, username, profile_link=""):
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO participants (giveaway_id,user_id,username,profile_link,joined_at) VALUES (?,?,?,?,?)",
                    (gid, user_id, username, profile_link, datetime.now().isoformat())
                )
            return False
        except sqlite3.IntegrityError:
            return True

    def get_participants(self, gid):
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM participants WHERE giveaway_id=? ORDER BY joined_at ASC", (gid,)
            ).fetchall()]

    def get_participant_count(self, gid):
        with self._conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM participants WHERE giveaway_id=?", (gid,)
            ).fetchone()[0]

    def get_participant_by_id(self, gid, user_id):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM participants WHERE giveaway_id=? AND user_id=?", (gid, user_id)
            ).fetchone()
            return dict(row) if row else None

    def set_secret_winner(self, gid, user_id):
        with self._conn() as conn:
            conn.execute("UPDATE giveaways SET secret_winner_id=? WHERE id=?", (user_id, gid))

    def get_secret_winner(self, gid):
        with self._conn() as conn:
            row = conn.execute("SELECT secret_winner_id FROM giveaways WHERE id=?", (gid,)).fetchone()
            return row["secret_winner_id"] if row else None

    def update_giveaway_message(self, gid, chat_id, message_id):
        with self._conn() as conn:
            conn.execute(
                "UPDATE giveaways SET post_chat_id=?, post_message_id=? WHERE id=?",
                (str(chat_id), message_id, gid)
            )

    def get_giveaway_post(self, gid):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT post_chat_id, post_message_id FROM giveaways WHERE id=?", (gid,)
            ).fetchone()
            if row and row["post_chat_id"]:
                return {"chat_id": row["post_chat_id"], "message_id": row["post_message_id"]}
            return None

    def finish_giveaway(self, gid, winner_ids):
        with self._conn() as conn:
            conn.execute("UPDATE giveaways SET status='finished' WHERE id=?", (gid,))
            for uid in winner_ids:
                conn.execute(
                    "INSERT INTO winners (giveaway_id,user_id,chosen_at) VALUES (?,?,?)",
                    (gid, uid, datetime.now().isoformat())
                )

    def cancel_giveaway(self, gid):
        with self._conn() as conn:
            conn.execute("UPDATE giveaways SET status='cancelled' WHERE id=?", (gid,))

    def set_captcha(self, user_id, gid, answer):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO captcha_sessions (user_id,giveaway_id,answer,created_at) VALUES (?,?,?,?)",
                (user_id, gid, answer, datetime.now().isoformat())
            )

    def get_captcha(self, user_id, gid):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT answer FROM captcha_sessions WHERE user_id=? AND giveaway_id=?", (user_id, gid)
            ).fetchone()
            return row["answer"] if row else None

    def clear_captcha(self, user_id, gid):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM captcha_sessions WHERE user_id=? AND giveaway_id=?", (user_id, gid)
            )

    def set_pending_publish(self, user_id, gid):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_publish (user_id,giveaway_id,created_at) VALUES (?,?,?)",
                (user_id, gid, datetime.now().isoformat())
            )

    def get_pending_publish(self, user_id):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT giveaway_id FROM pending_publish WHERE user_id=?", (user_id,)
            ).fetchone()
            return row["giveaway_id"] if row else None

    def clear_pending_publish(self, user_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM pending_publish WHERE user_id=?", (user_id,))


db = Database()
