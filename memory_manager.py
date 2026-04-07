import os
import json
import hashlib
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings
from datetime import datetime
import asyncio
from logger import logger

class MemoryManager:
    """
    Manages persistent long-term memory using Chroma DB vector storage.
    Stores conversations, thoughts, and learned patterns for AI improvement.
    """
    
    def __init__(self, db_path: str = "chroma_db"):
        self.db_path = db_path
        self.client = None
        self.collection = None
        self._initialize_db()
    
    def _initialize_db(self):
        """Initialize Chroma DB client and collection."""
        try:
            # Ensure the directory exists
            os.makedirs(self.db_path, exist_ok=True)
            
            # Initialize Chroma client with persistent storage
            self.client = chromadb.PersistentClient(
                path=self.db_path,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            
            # Get or create collection for storing memories
            self.collection = self.client.get_or_create_collection(
                name="discord_bot_memories",
                metadata={"description": "Long-term memory for Discord bot AI"}
            )
            
            logger.info("Chroma DB memory manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Chroma DB: {e}")
            # Fallback to in-memory if Chroma fails
            self.client = None
            self.collection = None
    
    def _generate_id(self, content: str, metadata: Dict[str, Any]) -> str:
        """Generate a unique ID for memory storage."""
        # Create a hash based on content and metadata for uniqueness
        content_hash = hashlib.md5(content.encode()).hexdigest()
        timestamp = datetime.now().isoformat()
        guild_id = metadata.get('guild_id', 'unknown')
        user_id = metadata.get('user_id', 'unknown')
        return f"{guild_id}_{user_id}_{content_hash}_{timestamp}"
    
    async def store_memory(self, content: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Store a memory in Chroma DB.
        
        Args:
            content: The text content to store
            metadata: Additional metadata (guild_id, user_id, timestamp, etc.)
            
        Returns:
            bool: Success status
        """
        if not self.collection:
            logger.warning("Chroma DB not available, skipping memory storage")
            return False
            
        try:
            if metadata is None:
                metadata = {}
            
            # Add timestamp if not present
            if 'timestamp' not in metadata:
                metadata['timestamp'] = datetime.now().isoformat()
            
            # Generate unique ID
            memory_id = self._generate_id(content, metadata)
            
            # Store in Chroma DB
            self.collection.add(
                documents=[content],
                metadatas=[metadata],
                ids=[memory_id]
            )
            
            logger.debug(f"Stored memory with ID: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            return False
    
    async def retrieve_memories(self, query: str, n_results: int = 5, 
                              filter_metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Retrieve relevant memories from Chroma DB.
        
        Args:
            query: The search query
            n_results: Number of results to return
            filter_metadata: Optional metadata filters
            
        Returns:
            List of memory documents with metadata
        """
        if not self.collection:
            logger.warning("Chroma DB not available, returning empty memories")
            return []
            
        try:
            # Prepare where clause for filtering
            where_clause = filter_metadata if filter_metadata else None
            
            # Query the collection
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_clause,
                include=['documents', 'metadatas', 'distances']
            )
            
            # Format results
            memories = []
            if results['documents'] and results['documents'][0]:
                for i, doc in enumerate(results['documents'][0]):
                    memory = {
                        'content': doc,
                        'metadata': results['metadatas'][0][i] if results['metadatas'] else {},
                        'distance': results['distances'][0][i] if results['distances'] else 0.0
                    }
                    memories.append(memory)
            
            logger.debug(f"Retrieved {len(memories)} memories for query: {query}")
            return memories
        except Exception as e:
            logger.error(f"Failed to retrieve memories: {e}")
            return []
    
    async def store_conversation_exchange(self, guild_id: int, user_id: int, 
                                        user_message: str, bot_response: str,
                                        reasoning: str = None, walkthrough: str = None) -> bool:
        """
        Store a conversation exchange with reasoning for long-term memory.
        
        Args:
            guild_id: The guild ID
            user_id: The user ID
            user_message: The user's message
            bot_response: The bot's response
            reasoning: The AI's reasoning process
            walkthrough: The AI's implementation walkthrough
            
        Returns:
            bool: Success status
        """
        try:
            # Create comprehensive memory content
            content_parts = [
                f"User Message: {user_message}",
                f"Bot Response: {bot_response}"
            ]
            
            if reasoning:
                content_parts.append(f"Reasoning: {reasoning}")
            if walkthrough:
                content_parts.append(f"Walkthrough: {walkthrough}")
            
            content = "\n\n".join(content_parts)
            
            metadata = {
                'guild_id': str(guild_id),
                'user_id': str(user_id),
                'type': 'conversation_exchange',
                'timestamp': datetime.now().isoformat()
            }
            
            return await self.store_memory(content, metadata)
        except Exception as e:
            logger.error(f"Failed to store conversation exchange: {e}")
            return False
    
    async def get_relevant_memories(self, guild_id: int, user_id: int, 
                                  current_input: str, n_results: int = 3) -> List[Dict[str, Any]]:
        """
        Get memories relevant to the current input for context enhancement.
        
        Args:
            guild_id: The guild ID
            user_id: The user ID
            current_input: The current user input
            n_results: Number of memories to retrieve
            
        Returns:
            List of relevant memories
        """
        filter_metadata = {
            'guild_id': str(guild_id)
        }
        
        # Optionally filter by user_id for personal memories
        # filter_metadata['user_id'] = str(user_id)
        
        return await self.retrieve_memories(
            query=current_input,
            n_results=n_results,
            filter_metadata=filter_metadata
        )
    
    async def store_ai_reflection(self, guild_id: int, user_id: int, 
                                original_request: str, original_response: str,
                                reflection: str, improvement: str) -> bool:
        """
        Store AI self-reflection for continuous improvement.
        
        Args:
            guild_id: The guild ID
            user_id: The user ID
            original_request: The original user request
            original_response: The AI's original response
            reflection: The AI's reflection on the response
            improvement: Suggested improvement
            
        Returns:
            bool: Success status
        """
        try:
            content_parts = [
                f"Original Request: {original_request}",
                f"Original Response: {original_response}",
                f"AI Reflection: {reflection}",
                f"Suggested Improvement: {improvement}"
            ]
            
            content = "\n\n---\n\n".join(content_parts)
            
            metadata = {
                'guild_id': str(guild_id),
                'user_id': str(user_id),
                'type': 'ai_reflection',
                'timestamp': datetime.now().isoformat()
            }
            
            return await self.store_memory(content, metadata)
        except Exception as e:
            logger.error(f"Failed to store AI reflection: {e}")
            return False
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the memory collection."""
        if not self.collection:
            return {"status": "unavailable"}
            
        try:
            count = self.collection.count()
            return {
                "status": "available",
                "total_memories": count,
                "collection_name": self.collection.name
            }
        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}")
            return {"status": "error", "error": str(e)}

# Global memory manager instance
memory_manager = MemoryManager()