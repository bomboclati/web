import os
import json
import aiohttp
import asyncio
from typing import List, Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential
from history_manager import history_manager

class AIClient:
    """
    Handles deep reasoning, web searches, and JSON extraction.
    Ensures the bot thinks before acting and provides a walkthrough.
    """
    def __init__(self, api_key: str, provider: str = "openrouter", model: Optional[str] = None):
        self.api_key = api_key
        self.provider = provider
        self.model = model or "meta-llama/llama-3.1-405b-instruct"
        self.base_urls = {
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        }

    async def get_search_results(self, query: str) -> str:
        """Performs a web search using Tavily or a fallback."""
        tavily_key = os.getenv("TAVILY_API_KEY")
        if not tavily_key:
            return "Web search is disabled. No Tavily API key found."
            
        async with aiohttp.ClientSession() as session:
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5
            }
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = []
                        for res in data.get("results", []):
                            results.append(f"- {res['title']}: {res['content']} ({res['url']})")
                        return "\n".join(results)
                    return f"Search error: {resp.status}"
            except Exception as e:
                return f"Web search failed: {str(e)}"

    def _build_enhanced_prompt(self, system_prompt: str, guild_id: int) -> str:
        """Build system prompt with action success/failure data for self-improvement."""
        from data_manager import dm
        
        successes = dm.get_guild_data(guild_id, "action_successes", {})
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        
        if not successes and not failures:
            return system_prompt
        
        improvement_data = "\n\nACTION PERFORMANCE HISTORY (use this to improve your plans):\n"
        
        if failures:
            improvement_data += "Previously failed actions (avoid or adjust):\n"
            for action, data in sorted(failures.items(), key=lambda x: x[1]["count"], reverse=True)[:10]:
                improvement_data += f"- `{action}`: Failed {data['count']} time(s). Last error: {data['last_error']}\n"
        
        if successes:
            improvement_data += "Previously successful actions (safe to reuse):\n"
            for action, count in sorted(successes.items(), key=lambda x: x[1], reverse=True)[:10]:
                improvement_data += f"- `{action}`: Succeeded {count} time(s)\n"
        
        improvement_data += "\nUse this data to avoid repeating mistakes and prefer proven action sequences."
        
        return system_prompt + improvement_data

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def chat(self, guild_id: int, user_id: int, user_input: str, system_prompt: str) -> Dict[str, Any]:
        """
        Communicates with the LLM, handles history, and processes web search requests.
        Includes self-improvement data from past action successes/failures.
        """
        history = history_manager.get_enhanced_context(guild_id, user_id, depth=int(os.getenv("MEMORY_DEPTH", 20)))
        
        enhanced_prompt = self._build_enhanced_prompt(system_prompt, guild_id)
        
        messages = [{"role": "system", "content": enhanced_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            if self.provider == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/antigravity"
                headers["X-Title"] = "Immortal AI Discord Bot"

            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
                # Force JSON output if the provider supports it
                "response_format": {"type": "json_object"} if self.provider in ["openai", "openrouter"] else None
            }

            async with session.post(self.base_urls.get(self.provider), headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"AI API Error ({resp.status}): {text}")
                
                data = await resp.json()
                ai_msg = data['choices'][0]['message']['content']

                # Try to parse JSON from AI message
                try:
                    # AI might include extra text; extract JSON using a helper if needed
                    # but with 'json_object' it should be clean.
                    res_json = json.loads(ai_msg)
                    
                    # Handle Web Search requested by AI
                    if res_json.get("action") == "web_search":
                        query = res_json.get("parameters", {}).get("query")
                        if query:
                            search_results = await self.get_search_results(query)
                            # Add search results as a 'system' or 'user' response and recurse
                            messages.append({"role": "assistant", "content": ai_msg})
                            messages.append({"role": "user", "content": f"Web Search Results for '{query}':\n{search_results}"})
                            
                            # Recursion with search context
                            payload["messages"] = messages
                            async with session.post(self.base_urls.get(self.provider), headers=headers, json=payload) as search_resp:
                                search_data = await search_resp.json()
                                ai_msg = search_data['choices'][0]['message']['content']
                                res_json = json.loads(ai_msg)
                    
                    return res_json
                except json.JSONDecodeError:
                    # If AI failed to return valid JSON, the retry logic will handle it
                    # or we can pass it back to a 'correction' filter
                    raise Exception("AI failed to return valid JSON.")

# Default System Prompt
SYSTEM_PROMPT = """
You are a creative, forward-thinking Discord bot AI with a continuous improvement mindset.
Every user request is an opportunity to deliver something super cool – beyond the bare minimum.

OUTPUT FORMAT:
You MUST output a valid JSON object with the following fields:
- "reasoning": Your deep chain-of-thought analysis (human-readable, bulleted).
- "walkthrough": A specific bulleted plan of action, ending with "Proceed?".
- "actions": A list of actions to perform. Each action has "name" and "parameters".
- "summary": A brief response to the user.

ACTION EXAMPLES:
- {"name": "create_channel", "parameters": {"name": "shop", "type": "text", "category": "Economy"}}
- {"name": "create_prefix_command", "parameters": {"name": "buy", "code": "..."}}
- {"name": "send_embed", "parameters": {"channel": "...", "title": "...", "description": "..."}}
- {"name": "web_search", "parameters": {"query": "..."}}

MANDATORY AUTO-DOCUMENTATION RULE:
Whenever you create any system (economy, leveling, tickets, verification, shop, appeals, staff, etc.), you MUST:
1. Create all necessary channels.
2. Create '!' prefix commands for user interaction.
3. Send a help embed in the relevant channel explaining the system and listing all '!' commands.
4. Create a '!help <system>' prefix command that shows the same embed.

IMMORTAL GUARANTEE:
Maintain state in data/ JSON files. Never lose a single bit of information.
"""
