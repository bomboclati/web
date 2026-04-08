import os
import json
import logging
from typing import List, Dict, Any, Optional
import hashlib
from datetime import datetime

try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

logger = logging.getLogger(__name__)

class VectorMemory:
    """
    Persistent long-term memory using Chroma DB for vector storage.
    Stores and retrieves conversations based on semantic similarity.
    """
    
    def __init__(self, persist_directory: str = "vector_db"):
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None
        self._initialize()
    
    def _initialize(self):
        """Initialize Chroma DB client and collection."""
        if not CHROMADB_AVAILABLE:
            logger.warning("ChromaDB not available. Vector memory disabled.")
            return
            
        try:
            # Ensure directory exists
            os.makedirs(self.persist_directory, exist_ok=True)
            
            # Initialize client
            self.client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            
            # Get or create collection for conversations
            self.collection = self.client.get_or_create_collection(
                name="conversations",
                metadata={"hnsw:space": "cosine"}
            )
            
            logger.info(f"Vector memory initialized with {self.collection.count()} stored conversations")
            
        except Exception as e:
            logger.error(f"Failed to initialize vector memory: {e}")
            self.client = None
            self.collection = None
    
    def _generate_id(self, guild_id: int, user_id: int, timestamp: float) -> str:
        """Generate a unique ID for a conversation entry."""
        data = f"{guild_id}_{user_id}_{timestamp}"
        return hashlib.md5(data.encode()).hexdigest()
    
    def store_conversation(
        self, 
        guild_id: int, 
        user_id: int, 
        user_message: str, 
        bot_response: str,
        reasoning: str = "",
        walkthrough: str = "",
        importance_score: float = 0.5
    ):
        """
        Store a conversation exchange in vector memory.
        
        Args:
            guild_id: Discord guild ID
            user_id: Discord user ID
            user_message: User's message
            bot_response: Bot's response
            reasoning: AI's reasoning process
            walkthrough: AI's planned walkthrough
            importance_score: Calculated importance (0.0-1.0)
        """
        if not self.client or not self.collection:
            return
            
        try:
            timestamp = datetime.now().timestamp()
            doc_id = self._generate_id(guild_id, user_id, timestamp)
            
            # Create document text for embedding
            document_text = f"""
User: {user_message}
Assistant: {bot_response}
Reasoning: {reasoning}
Walkthrough: {walkthrough}
            """.strip()
            
            # Metadata for filtering and retrieval
            metadata = {
                "guild_id": str(guild_id),
                "user_id": str(user_id),
                "timestamp": str(timestamp),
                "importance_score": str(importance_score),
                "has_reasoning": str(bool(reasoning)),
                "has_walkthrough": str(bool(walkthrough))
            }
            
            # Store in collection
            self.collection.add(
                documents=[document_text],
                metadatas=[metadata],
                ids=[doc_id]
            )
            
            logger.debug(f"Stored conversation in vector memory: {doc_id}")
            
        except Exception as e:
            logger.error(f"Failed to store conversation in vector memory: {e}")
    
    def retrieve_relevant_conversations(
        self,
        guild_id: int,
        user_id: int,
        query: str,
        n_results: int = 5,
        min_importance: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve conversations relevant to the current query.
        
        Args:
            guild_id: Discord guild ID (for filtering)
            user_id: Discord user ID (for filtering)
            query: Current user query to find similar conversations
            n_results: Maximum number of results to return
            min_importance: Minimum importance score threshold
            
        Returns:
            List of relevant conversation dictionaries
        """
        if not self.client or not self.collection:
            return []
            
        try:
            # Build where clause for filtering
            where_conditions = []
            
            # Always filter by guild for server-specific memory
            where_conditions.append({"guild_id": {"$eq": str(guild_id)}})
            
            # Optionally filter by user for personal memory
            # where_conditions.append({"user_id": {"$eq": str(user_id)}})
            
            # Filter by minimum importance
            where_conditions.append({"importance_score": {"$gte": str(min_importance)}})
            
            # Combine conditions (ChromaDB uses AND for multiple conditions)
            where_clause = {"$and": where_conditions} if len(where_conditions) > 1 else where_conditions[0]
            
            # Query the collection
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_clause,
                include=["documents", "metadatas", "distances"]
            )
            
            # Format results
            conversations = []
            if results['ids'] and len(results['ids'][0]) > 0:
                for i in range(len(results['ids'][0])):
                    conv = {
                        "id": results['ids'][0][i],
                        "document": results['documents'][0][i],
                        "metadata": results['metadatas'][0][i],
                        "similarity": 1 - results['distances'][0][i]  # Convert distance to similarity
                    }
                    conversations.append(conv)
            
            logger.debug(f"Retrieved {len(conversations)} relevant conversations from vector memory")
            return conversations
            
        except Exception as e:
            logger.error(f"Failed to retrieve conversations from vector memory: {e}")
            return []
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """Get statistics about the vector memory."""
        if not self.client or not self.collection:
            return {"status": "disabled", "count": 0}
            
        try:
            count = self.collection.count()
            return {
                "status": "active",
                "count": count,
                "persist_directory": self.persist_directory
            }
        except Exception as e:
            logger.error(f"Failed to get vector memory stats: {e}")
            return {"status": "error", "count": 0, "error": str(e)}


# Global vector memory instance
vector_memory = VectorMemory()