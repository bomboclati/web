import json
from typing import List, Dict, Optional
from data_manager import dm

class HistoryManager:
    """
    Manages the 'Infinite Memory' system.
    Stores every exchange ever made, but limits active context to X recent exchanges.
    """
    def __init__(self, history_file: str = "conversation_history"):
        self.history_file = history_file

    def _get_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}_{user_id}"

    def add_exchange(self, guild_id: int, user_id: int, user_msg: str, bot_response: str):
        """Adds a message pair to the infinite history and writes to disk immediately."""
        key = self._get_key(guild_id, user_id)
        history = dm.load_json(self.history_file, default={})
        
        if key not in history:
            history[key] = []
        
        history[key].append({
            "role": "user",
            "content": user_msg
        })
        history[key].append({
            "role": "assistant",
            "content": bot_response
        })
        
        dm.save_json(self.history_file, history)

    def get_context(self, guild_id: int, user_id: int, depth: int = 20) -> List[Dict[str, str]]:
        """Retrieves the last N exchanges for the LLM context."""
        key = self._get_key(guild_id, user_id)
        history = dm.load_json(self.history_file, default={})
        
        if key not in history:
            return []
        
        # history[key] is a list of {"role": "...", "content": "..."}
        # depth * 2 because each exchange has 2 entries (user & assistant)
        return history[key][-(depth * 2):]

    def clear_history(self, guild_id: int, user_id: int):
        key = self._get_key(guild_id, user_id)
        history = dm.load_json(self.history_file, default={})
        if key in history:
            del history[key]
            dm.save_json(self.history_file, history)

# Initialize global HistoryManager
history_manager = HistoryManager()
