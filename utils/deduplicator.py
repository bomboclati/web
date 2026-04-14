import time
from typing import Dict, Any

class MessageDeduplicator:
    """
    Prevents duplicate messages (especially DMs) from being sent 
    multiple times in a short interval due to bot restarts 
    or logic loops.
    """
    def __init__(self):
        self._cache: Dict[str, float] = {}  # key -> expiry_timestamp

    def should_send(self, message_key: str, interval: int = 60) -> bool:
        """
        Check if a message with the given key should be sent.
        Returns True if it's okay to send (not a duplicate), False otherwise.
        """
        now = time.time()
        
        # Cleanup expired entries periodically (10% chance per call)
        if now % 10 < 1:
            self._cleanup()
            
        if message_key in self._cache:
            if now < self._cache[message_key]:
                return False
        
        self._cache[message_key] = now + interval
        return True

    def should_skip_action(self, guild_id: int, action_name: str, params: dict, interval: int = 30) -> bool:
        """
        Check if an action with the given parameters should be skipped (recently executed).
        Returns True if action should be skipped (duplicate), False if it's okay to execute.
        """
        import hashlib
        import json
        
        # Create a stable hash key from guild, action name, and params
        param_hash = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()
        action_key = f"action:{guild_id}:{action_name}:{param_hash}"
        
        # Reuse the existing should_send logic (inverse result)
        return not self.should_send(action_key, interval)

    def _cleanup(self):
        now = time.time()
        self._cache = {k: v for k, v in self._cache.items() if v > now}

# Global singleton
deduplicator = MessageDeduplicator()
