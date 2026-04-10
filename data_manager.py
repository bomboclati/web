import json
import os
import shutil
import sqlite3
import aiosqlite
from typing import Any, Dict, Optional, List, Tuple
import threading
import time
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import base64
import hashlib

from logger import logger

class DataManager:
    """
    Atomic writing system to ensure Zero Data Loss.
    Ensures state is written to disk immediately and safely.
    Now includes SQLite backend option for better performance.
    """
    def __init__(self, data_dir: str = "data", use_sqlite: bool = False):
        self.data_dir = data_dir
        self.use_sqlite = use_sqlite
        self._lock = threading.Lock()
        
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        if self.use_sqlite:
            self._init_sqlite()

    def _get_path(self, filename: str) -> str:
        if not filename.endswith(".json"):
            filename += ".json"
        return os.path.join(self.data_dir, filename)

    def _init_sqlite(self):
        """Initialize SQLite database for history storage"""
        self.db_path = os.path.join(self.data_dir, "conversation_history.db")
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    importance_score REAL DEFAULT 0.5
                )
            """)
            
            # Create indexes for better query performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_guild_user_timestamp 
                ON exchanges(guild_id, user_id, timestamp DESC)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON exchanges(timestamp DESC)
            """)
            
            # Create table for summarized conversations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    start_timestamp REAL NOT NULL,
                    end_timestamp REAL NOT NULL,
                    summary_text TEXT NOT NULL,
                    message_count INTEGER NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            
            conn.commit()
            conn.close()

    def save_json(self, filename: str, data: Any):
        """
        Atomic Write: Writes to a temporary file, then renames it.
        This prevents data corruption if the bot crashes during the write.
        """
        if self.use_sqlite and filename == "conversation_history":
            # For history, we'll use SQLite instead
            return
            
        path = self._get_path(filename)
        temp_path = f"{path}.tmp"

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        # Replace original with the new temp file
        os.replace(temp_path, path)

    def load_json(self, filename: str, default: Any = None) -> Any:
        path = self._get_path(filename)
        if not os.path.exists(path):
            return default if default is not None else {}
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error("Corrupted JSON in %s: %s", filename, e)
            return default if default is not None else {}
        except IOError as e:
            logger.critical("IO error reading %s: %s — DATA LOSS RISK", filename, e)
            raise

async def save_exchange(self, guild_id: int, user_id: int, role: str, content: str, importance_score: float = 0.5):
        """Save a single exchange to SQLite database"""
        if not self.use_sqlite:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO exchanges (guild_id, user_id, role, content, timestamp, importance_score) VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, role, content, time.time(), importance_score)
            )
            await db.commit()
        return True

    async def load_exchanges(self, guild_id: int, user_id: int, limit: Optional[int] = None, 
                          start_time: Optional[float] = None, end_time: Optional[float] = None) -> List[Dict]:
        """Load exchanges from SQLite with optional filtering"""
        if not self.use_sqlite:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT role, content, timestamp, importance_score FROM exchanges WHERE guild_id=? AND user_id=?"
            params = [guild_id, user_id]
            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(end_time)
            query += " ORDER BY timestamp DESC"
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [
            {"role": r["role"], "content": r["content"], "timestamp": r["timestamp"], "importance_score": r["importance_score"]}
            for r in reversed(rows)
        ]

async def save_conversation_summary(self, guild_id: int, user_id: int, 
                                  end_timestamp: float, summary_text: str, message_count: int):
        """Save a conversation summary"""
        if not self.use_sqlite:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO conversation_summaries 
                   (guild_id, user_id, start_timestamp, end_timestamp, summary_text, message_count, created_at) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, user_id, time.time() - (message_count * 2), end_timestamp, summary_text, message_count, time.time())
            )
            await db.commit()
        return True

    async def load_conversation_summaries(self, guild_id: int, user_id: int, 
                                       start_time: Optional[float] = None, 
                                       end_time: Optional[float] = None) -> List[Dict]:
        """Load conversation summaries"""
        if not self.use_sqlite:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT start_timestamp, end_timestamp, summary_text, message_count, created_at FROM conversation_summaries WHERE guild_id=? AND user_id=?"
            params = [guild_id, user_id]
            if start_time is not None:
                query += " AND end_timestamp >= ?"
                params.append(start_time)
            if end_time is not None:
                query += " AND start_timestamp <= ?"
                params.append(end_time)
            query += " ORDER BY start_timestamp DESC"
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [
            {"start_timestamp": r["start_timestamp"], "end_timestamp": r["end_timestamp"], 
             "summary_text": r["summary_text"], "message_count": r["message_count"], "created_at": r["created_at"]}
            for r in rows
        ]

    def update_guild_data(self, guild_id: int, key: str, value: Any):
        """Helper to update a specific guild's data block in a large file."""
        filename = f"guild_{guild_id}.json"
        data = self.load_json(filename)
        data[key] = value
        self.save_json(filename, data)

    def get_guild_data(self, guild_id: int, key: str, default: Any = None) -> Any:
        filename = f"guild_{guild_id}.json"
        data = self.load_json(filename)
        return data.get(key, default)

    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key from environment or generate one"""
        key_env = os.getenv("ENCRYPTION_KEY")
        if key_env:
            # Use provided key (must be base64 encoded 32 bytes)
            return base64.urlsafe_b64encode(hashlib.sha256(key_env.encode()).digest())
        
        # Check for stored key or generate new one
        key_file = os.path.join(self.data_dir, ".key")
        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                return f.read()
        
        # Generate new key and store it
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
        return key

    def set_guild_api_key(self, guild_id: int, api_key: str, provider: str = "openrouter"):
        """Set guild-specific API key (encrypted)"""
        f = Fernet(self._get_encryption_key())
        encrypted_key = f.encrypt(api_key.encode()).decode()
        
        api_keys = self.load_json("guild_api_keys", default={})
        api_keys[str(guild_id)] = {
            "api_key": encrypted_key,
            "provider": provider,
            "updated_at": datetime.now().isoformat()
        }
        self.save_json("guild_api_keys", api_keys)

    def get_guild_api_key(self, guild_id: int) -> Optional[Dict[str, str]]:
        """Get guild-specific API key (decrypted)"""
        api_keys = self.load_json("guild_api_keys", default={})
        guild_data = api_keys.get(str(guild_id))
        
        if not guild_data:
            return None
        
        # Decrypt the API key
        try:
            f = Fernet(self._get_encryption_key())
            decrypted_key = f.decrypt(guild_data["api_key"].encode()).decode()
            return {
                "api_key": decrypted_key,
                "provider": guild_data.get("provider", "openrouter")
            }
        except Exception:
            # If decryption fails, return as-is (might be old unencrypted key)
            return guild_data

    def record_global_action_result(self, action_name: str, success: bool, error: str = None):
        """Record anonymized action result for cross-server intelligence sharing."""
        intelligence = self.load_json("global_intelligence", default={})
        
        if "actions" not in intelligence:
            intelligence["actions"] = {}
        
        if action_name not in intelligence["actions"]:
            intelligence["actions"][action_name] = {
                "successes": 0,
                "failures": 0,
                "total_guilds": 0,
                "errors": {},
                "last_updated": 0
            }
        
        action_data = intelligence["actions"][action_name]
        
        if success:
            action_data["successes"] += 1
        else:
            action_data["failures"] += 1
            if error:
                error_key = error[:100]
                if error_key not in action_data["errors"]:
                    action_data["errors"][error_key] = 0
                action_data["errors"][error_key] += 1
        
        action_data["total_guilds"] = action_data["successes"] + action_data["failures"]
        action_data["last_updated"] = time.time()
        
        self.save_json("global_intelligence", intelligence)

    def get_global_intelligence(self, min_guilds: int = 1) -> Dict[str, Any]:
        """Get aggregated cross-server intelligence for AI prompt injection."""
        intelligence = self.load_json("global_intelligence", default={"actions": {}})
        actions = intelligence.get("actions", {})
        
        filtered = {}
        for action_name, data in actions.items():
            if data["total_guilds"] >= min_guilds:
                success_rate = data["successes"] / max(data["total_guilds"], 1)
                top_errors = sorted(data.get("errors", {}).items(), key=lambda x: x[1], reverse=True)[:3]
                
                filtered[action_name] = {
                    "success_rate": round(success_rate, 2),
                    "total_uses": data["total_guilds"],
                    "top_errors": [{"error": e, "count": c} for e, c in top_errors] if top_errors else []
                }
        
        return filtered

    def backup_data(self, backup_dir: str = "backups"):
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
         
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = ""  # Initialize to avoid unbound variable
        
        if self.use_sqlite:
            # Backup SQLite database
            backup_path = os.path.join(backup_dir, f"history_backup_{timestamp}.db")
            shutil.copy2(self.db_path, backup_path)
        else:
            # Backup JSON files
            backup_path = os.path.join(backup_dir, f"backup_{timestamp}.zip")
            shutil.make_archive(os.path.join(backup_dir, f"backup_{timestamp}"), 'zip', self.data_dir)
        
        # Verify backup
        if not os.path.exists(backup_path):
            raise Exception("Backup verification failed")

    async def cleanup_old_data(self, days_to_keep: int = 30):
        """Remove data older than specified days"""
        cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)
        
        if self.use_sqlite:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM exchanges WHERE timestamp < ?", (cutoff_time,))
                await db.execute("DELETE FROM conversation_summaries WHERE created_at < ?", (cutoff_time,))
                await db.commit()
        else:
            for f in os.listdir(self.data_dir):
                if f.startswith("guild_") and f.endswith(".json"):
                    filename = f[:-5]
                    data = self.load_json(filename, {})
                    history = data.get("conversation_history", {})
                    changed = False
                    for uid in list(history.keys()):
                        before = len(history[uid])
                        history[uid] = [e for e in history[uid] if e.get("timestamp", 0) > cutoff_time]
                        if len(history[uid]) != before:
                            changed = True
                    if changed:
                        data["conversation_history"] = history
                        self.save_json(filename, data)

    async def export_memory(self, guild_id: int = None) -> dict:
        """Export conversation memory as JSON (for backup/migration)."""
        export_data = {"version": "1.0", "exported_at": datetime.now().isoformat(), "guilds": {}}
        
        if self.use_sqlite:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                if guild_id:
                    guild_ids = [guild_id]
                else:
                    async with db.execute("SELECT DISTINCT guild_id FROM exchanges") as cursor:
                        guild_ids = [r[0] for r in await cursor.fetchall()]
                
                for gid in guild_ids:
                    async with db.execute("SELECT DISTINCT user_id FROM exchanges WHERE guild_id=?", (gid,)) as cursor:
                        users = [r[0] for r in await cursor.fetchall()]
                    export_data["guilds"][str(gid)] = {"users": {}}
                    
                    for uid in users:
                        async with db.execute("SELECT role, content, timestamp, importance_score FROM exchanges WHERE guild_id=? AND user_id=? ORDER BY timestamp", (gid, uid)) as cursor:
                            exchanges = await cursor.fetchall()
                        export_data["guilds"][str(gid)]["users"][str(uid)] = {
                            "exchanges": [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"], "importance_score": r["importance_score"]} for r in exchanges]
                        }
        else:
            if guild_id:
                data = self.load_json(f"guild_{guild_id}", {})
                export_data["guilds"][str(guild_id)] = data.get("conversation_history", {})
            else:
                for f in os.listdir(self.data_dir):
                    if f.startswith("guild_") and f.endswith(".json"):
                        gid = f.replace("guild_", "").replace(".json", "")
                        if gid.isdigit():
                            data = self.load_json(f[:-5], {})
                            export_data["guilds"][gid] = data.get("conversation_history", {})
        
        return export_data

    async def import_memory(self, import_data: dict, merge: bool = True) -> dict:
        """Import conversation memory from JSON export."""
        result = {"success": True, "imported": 0, "skipped": 0, "errors": []}
        
        if not self.use_sqlite:
            result["success"] = False
            result["errors"].append("Import only supported with SQLite backend")
            return result
        
        guilds = import_data.get("guilds", {})
        async with aiosqlite.connect(self.db_path) as db:
            for gid_str, guild_data in guilds.items():
                try:
                    gid = int(gid_str)
                    for uid_str, user_data in guild_data.get("users", {}).items():
                        uid = int(uid_str)
                        for ex in user_data.get("exchanges", []):
                            async with db.execute("SELECT id FROM exchanges WHERE guild_id=? AND user_id=? AND timestamp=? AND role=?", (gid, uid, ex.get("timestamp"), ex.get("role"))) as cursor:
                                existing = await cursor.fetchone()
                            if not existing:
                                await db.execute("INSERT INTO exchanges (guild_id, user_id, role, content, timestamp, importance_score) VALUES (?, ?, ?, ?, ?, ?)", (gid, uid, ex.get("role", "user"), ex.get("content", ""), ex.get("timestamp", time.time()), ex.get("importance_score", 0.5)))
                                result["imported"] += 1
                            else:
                                result["skipped"] = result.get("skipped", 0) + 1
                except Exception as e:
                    result["errors"].append(f"Guild {gid_str}: {str(e)}")
            await db.commit()
        
        return result

        if not self.use_sqlite:
            result["success"] = False
            result["errors"].append("Import only supported with SQLite backend")
            return result

        conn = sqlite3.connect(self.db_path, check_same_thread=False)

        for gid_str, guild_data in guilds.items():
            try:
                gid = int(gid_str)
                users = guild_data.get("users", {})

                for uid_str, user_data in users.items():
                    uid = int(uid_str)
                    exchanges = user_data.get("exchanges", [])

                    for ex in exchanges:
                        cursor = conn.execute(
                            "SELECT id FROM exchanges WHERE guild_id=? AND user_id=? AND timestamp=? AND role=?",
                            (gid, uid, ex.get("timestamp"), ex.get("role", "user"))
                        )
                        existing = cursor.fetchone()
                        
                        if not existing:
                            conn.execute(
                                "INSERT INTO exchanges (guild_id, user_id, role, content, timestamp, importance_score) VALUES (?, ?, ?, ?, ?, ?)",
                                (gid, uid, ex.get("role", "user"), ex.get("content", ""), ex.get("timestamp", time.time()), ex.get("importance_score", 0.5))
                            )
                            result["imported"] += 1
                        else:
                            result["skipped"] = result.get("skipped", 0) + 1

                    summaries = user_data.get("summaries", [])
                    for s in summaries:
                        conn.execute(
                            "INSERT INTO conversation_summaries (guild_id, user_id, start_timestamp, end_timestamp, summary_text, message_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (gid, uid, s.get("start"), s.get("end"), s.get("summary", ""), s.get("count", 0), time.time())
                        )

            except Exception as e:
                result["errors"].append(f"Guild {gid_str}: {str(e)}")

        conn.commit()
        conn.close()

        if not result["errors"]:
            logger.info(f"Memory import completed: {result['imported']} exchanges")
        else:
            logger.error(f"Memory import completed with errors: {result['errors']}")

        return result


# Initialize global DataManager
# Use SQLite by default for better performance, but can be overridden
use_sqlite = os.getenv("USE_SQLITE", "true").lower() == "true"
dm = DataManager(use_sqlite=use_sqlite)