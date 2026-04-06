import json
import os
import shutil
from typing import Any, Dict, Optional

class DataManager:
    """
    Atomic writing system to ensure Zero Data Loss.
    Ensures state is written to disk immediately and safely.
    """
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

    def _get_path(self, filename: str) -> str:
        if not filename.endswith(".json"):
            filename += ".json"
        return os.path.join(self.data_dir, filename)

    def save_json(self, filename: str, data: Any):
        """
        Atomic Write: Writes to a temporary file, then renames it.
        This prevents data corruption if the bot crashes during the write.
        """
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
        except (json.JSONDecodeError, IOError):
            # If load fails, check for backup if implemented, else return default
            return default if default is not None else {}

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

    def backup_data(self, backup_dir: str = "backups"):
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.make_archive(os.path.join(backup_dir, f"backup_{timestamp}"), 'zip', self.data_dir)

# Initialize global DataManager
dm = DataManager()
