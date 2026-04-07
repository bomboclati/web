import json
import sqlite3
import os
from typing import List, Dict, Optional
from data_manager import dm
import time
import re

class HistoryManager:
    """
    Manages the 'Infinite Memory' system with hierarchical storage.
    Stores recent exchanges in full and summarizes older exchanges.
    Supports both JSON and SQLite backends.
    """
    def __init__(self, history_file: str = "conversation_history"):
        self.history_file = history_file
        # Configuration from environment variables with defaults
        self.recent_depth = int(os.getenv("RECENT_MEMORY_DEPTH", "10"))  # Recent exchanges kept in full
        self.summary_threshold = int(os.getenv("SUMMARY_THRESHOLD", "50"))  # Summarize after this many exchanges
        self.max_summary_age_days = int(os.getenv("MAX_SUMMARY_AGE_DAYS", "30"))  # Keep summaries this long

    def _get_key(self, guild_id: int, user_id: int) -> str:
        return f"{guild_id}_{user_id}"

    def _calculate_importance_score(self, user_msg: str, bot_response: str) -> float:
        """Calculate importance score for an exchange based on various factors"""
        score = 0.5  # Base score
        
        # Length factor - longer exchanges might be more important
        total_length = len(user_msg) + len(bot_response)
        if total_length > 200:
            score += 0.2
        elif total_length > 100:
            score += 0.1
            
        # Question factor - exchanges with questions might be more important
        if "?" in user_msg:
            score += 0.1
            
        # Certain keywords that might indicate importance
        important_keywords = ["remember", "important", "note", "task", "todo", "deadline", "meeting"]
        combined_text = (user_msg + " " + bot_response).lower()
        for keyword in important_keywords:
            if keyword in combined_text:
                score += 0.05
                break  # Only add once
                
        # Cap the score between 0.1 and 1.0
        return max(0.1, min(1.0, score))

    def _should_summarize(self, guild_id: int, user_id: int) -> bool:
        """Determine if we should create a summary of older exchanges"""
        if not dm.use_sqlite:
            # For JSON backend, check file-based history
            history = dm.load_json(self.history_file, default={})
            key = self._get_key(guild_id, user_id)
            if key not in history:
                return False
            return len(history[key]) >= self.summary_threshold * 2  # *2 because each exchange has 2 entries
        else:
            # For SQLite backend, count exchanges
            exchanges = dm.load_exchanges(guild_id, user_id)
            return len(exchanges) >= self.summary_threshold

    def _create_summary(self, guild_id: int, user_id: int) -> bool:
        """Create a summary of older exchanges and remove them from active storage"""
        try:
            if dm.use_sqlite:
                # Get exchanges that are candidates for summarization (older ones)
                all_exchanges = dm.load_exchanges(guild_id, user_id)
                if len(all_exchanges) < self.summary_threshold:
                    return False
                    
                # Take older exchanges for summarization (keep recent ones)
                exchanges_to_summarize = all_exchanges[:-self.recent_depth*2] if len(all_exchanges) > self.recent_depth*2 else all_exchanges[:len(all_exchanges)//2]
                recent_exchanges = all_exchanges[-self.recent_depth*2:] if len(all_exchanges) > self.recent_depth*2 else []
                
                if not exchanges_to_summarize:
                    return False
                    
                # Create summary text
                summary_parts = []
                for exchange in exchanges_to_summarize:
                    role = exchange["role"]
                    content = exchange["content"]
                    # Truncate very long messages
                    if len(content) > 100:
                        content = content[:97] + "..."
                    summary_parts.append(f"{role}: {content}")
                
                summary_text = " | ".join(summary_parts)
                
                # Save summary
                start_timestamp = exchanges_to_summarize[0]["timestamp"] if exchanges_to_summarize else time.time()
                end_timestamp = exchanges_to_summarize[-1]["timestamp"] if exchanges_to_summarize else time.time()
                
                dm.save_conversation_summary(
                    guild_id, user_id, 
                    start_timestamp, end_timestamp,
                    summary_text, 
                    len(exchanges_to_summarize)
                )
                
                # For SQLite, we don't actually delete the exchanges here to maintain ability to search
                # In a production system, we might move them to archive or delete after verifying summary
                return True
            else:
                # JSON backend summarization (simpler approach)
                history = dm.load_json(self.history_file, default={})
                key = self._get_key(guild_id, user_id)
                if key not in history or len(history[key]) < self.summary_threshold * 2:
                    return False
                
                # Take older exchanges for summarization
                exchanges_to_summarize = history[key][:-self.recent_depth*2] if len(history[key]) > self.recent_depth*2 else history[key][:len(history[key])//2]
                if not exchanges_to_summarize:
                    return False
                
                # Create summary
                summary_parts = []
                for i in range(0, len(exchanges_to_summarize), 2):  # Process in pairs
                    if i+1 < len(exchanges_to_summarize):
                        user_content = exchanges_to_summarize[i].get("content", "")
                        bot_content = exchanges_to_summarize[i+1].get("content", "")
                        summary_parts.append(f"User: {user_content[:50]}... | Bot: {bot_content[:50]}...")
                
                summary_text = " | ".join(summary_parts)
                
                # In a full implementation, we'd save this to a summaries file
                # For now, we'll just truncate the history and note that summarization happened
                # Keep only recent exchanges
                history[key] = history[key][-self.recent_depth*2:]
                dm.save_json(self.history_file, history)
                return True
                
        except Exception as e:
            print(f"Error creating summary: {e}")
            return False

    def add_exchange(self, guild_id: int, user_id: int, user_msg: str, bot_response: str):
        """Adds a message pair to the infinite history and writes to disk immediately."""
        # Calculate importance score
        importance_score = self._calculate_importance_score(user_msg, bot_response)
        
        # Check if we should summarize older exchanges
        if self._should_summarize(guild_id, user_id):
            self._create_summary(guild_id, user_id)
        
        # Store the exchange
        if dm.use_sqlite:
            # Store in SQLite with importance score
            dm.save_exchange(guild_id, user_id, "user", user_msg, importance_score)
            dm.save_exchange(guild_id, user_id, "assistant", bot_response, importance_score)
        else:
            # Original JSON storage for backward compatibility
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
        """Retrieves the last N exchanges for the LLM context, with importance weighting."""
        if dm.use_sqlite:
            # Get recent exchanges from SQLite
            exchanges = dm.load_exchanges(guild_id, user_id, limit=depth*2)
            
            # Also get summaries for additional context if needed
            summaries = dm.load_conversation_summaries(guild_id, user_id)
            
            # Format exchanges for compatibility with existing code
            formatted_exchanges = []
            for exchange in reversed(exchanges):  # Reverse to get chronological order
                formatted_exchanges.append({
                    "role": exchange["role"],
                    "content": exchange["content"]
                })
                
            return formatted_exchanges
        else:
            # Original JSON backend
            key = self._get_key(guild_id, user_id)
            history = dm.load_json(self.history_file, default={})
            
            if key not in history:
                return []
            
            # history[key] is a list of {"role": "...", "content": "..."}
            # depth * 2 because each exchange has 2 entries (user & assistant)
            return history[key][-(depth * 2):]

    def get_enhanced_context(self, guild_id: int, user_id: int, depth: int = 20) -> List[Dict[str, str]]:
        """Get context enhanced with summaries for better memory utilization"""
        if not dm.use_sqlite:
            return self.get_context(guild_id, user_id, depth)
            
        recent_exchanges = dm.load_exchanges(guild_id, user_id, limit=depth*2)
        summaries = dm.load_conversation_summaries(guild_id, user_id)
        
        formatted_exchanges = []
        
        if summaries:
            summary_parts = []
            for s in summaries[-5:]:
                summary_parts.append(s["summary_text"])
            combined_summary = "\nPrevious conversation summary:\n" + "\n".join(summary_parts)
            formatted_exchanges.append({
                "role": "system",
                "content": combined_summary
            })
        
        for exchange in reversed(recent_exchanges):
            formatted_exchanges.append({
                "role": exchange["role"],
                "content": exchange["content"]
            })
        
        return formatted_exchanges

    def search_history(self, guild_id: int, user_id: int, query: str, 
                      start_time: Optional[float] = None, 
                      end_time: Optional[float] = None) -> List[Dict]:
        """Search conversation history for relevant exchanges"""
        if not dm.use_sqlite:
            # For JSON backend, do simple text search
            history = dm.load_json(self.history_file, default={})
            key = self._get_key(guild_id, user_id)
            if key not in history:
                return []
                
            results = []
            query_lower = query.lower()
            for i in range(0, len(history[key]), 2):  # Process in pairs (user, bot)
                if i+1 < len(history[key]):
                    user_msg = history[key][i].get("content", "")
                    bot_msg = history[key][i+1].get("content", "")
                    combined = (user_msg + " " + bot_msg).lower()
                    if query_lower in combined:
                        results.append({
                            "role": "user",
                            "content": user_msg,
                            "timestamp": 0  # JSON doesn't store timestamps
                        })
                        results.append({
                            "role": "assistant", 
                            "content": bot_msg,
                            "timestamp": 0
                        })
            return results
        else:
            # For SQLite backend, use SQL search
            with dm._lock:
                conn = sqlite3.connect(dm.db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                
                query_sql = """
                    SELECT role, content, timestamp, importance_score 
                    FROM exchanges 
                    WHERE guild_id = ? AND user_id = ? 
                    AND (content LIKE ? OR content LIKE ?)
                """
                params = [guild_id, user_id, f"%{query}%", f"%{query}%"]
                
                if start_time is not None:
                    query_sql += " AND timestamp >= ?"
                    params.append(start_time)
                    
                if end_time is not None:
                    query_sql += " AND timestamp <= ?"
                    params.append(end_time)
                    
                query_sql += " ORDER BY timestamp DESC"
                
                cursor = conn.execute(query_sql, params)
                rows = cursor.fetchall()
                conn.close()
                
                results = []
                for row in rows:
                    results.append({
                        "role": row["role"],
                        "content": row["content"],
                        "timestamp": row["timestamp"],
                        "importance_score": row["importance_score"]
                    })
                    
                return results

    def clear_history(self, guild_id: int, user_id: int):
        key = self._get_key(guild_id, user_id)
        if dm.use_sqlite:
            with dm._lock:
                conn = sqlite3.connect(dm.db_path, check_same_thread=False)
                conn.execute("DELETE FROM exchanges WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
                conn.execute("DELETE FROM conversation_summaries WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
                conn.commit()
                conn.close()
        else:
            history = dm.load_json(self.history_file, default={})
            if key in history:
                del history[key]
                dm.save_json(self.history_file, history)

# Import os for environment variables
import os

# Initialize global HistoryManager
history_manager = HistoryManager()