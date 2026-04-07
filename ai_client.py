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
        """Build system prompt with action success/failure data and command usage for self-improvement."""
        from data_manager import dm
        
        successes = dm.get_guild_data(guild_id, "action_successes", {})
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        global_intel = dm.get_global_intelligence(min_guilds=2)
        command_usage = dm.get_guild_data(guild_id, "command_usage", {})
        
        if not successes and not failures and not global_intel and not command_usage:
            return system_prompt
        
        improvement_data = "\n\nACTION PERFORMANCE HISTORY (use this to improve your plans):\n"
        
        if failures:
            improvement_data += "Your previously failed actions on this server (avoid or adjust):\n"
            for action, data in sorted(failures.items(), key=lambda x: x[1]["count"], reverse=True)[:5]:
                improvement_data += f"- `{action}`: Failed {data['count']} time(s). Last error: {data['last_error']}\n"
        
        if successes:
            improvement_data += "Your previously successful actions on this server (safe to reuse):\n"
            for action, count in sorted(successes.items(), key=lambda x: x[1], reverse=True)[:5]:
                improvement_data += f"- `{action}`: Succeeded {count} time(s)\n"
        
        if global_intel:
            improvement_data += "\nCross-server intelligence (aggregated from all servers):\n"
            for action, data in sorted(global_intel.items(), key=lambda x: x[1]["total_uses"], reverse=True)[:10]:
                rate = data["success_rate"]
                status = "HIGHLY RELIABLE" if rate >= 0.9 else "MODERATE" if rate >= 0.7 else "UNRELIABLE - AVOID"
                improvement_data += f"- `{action}`: {rate*100:.0f}% success rate across {data['total_uses']} uses [{status}]\n"
                if data["top_errors"]:
                    for err in data["top_errors"]:
                        improvement_data += f"  Common error: {err['error']} ({err['count']}x)\n"
        
        if command_usage:
            improvement_data += "\nCommand usage on this server (creates commands users actually want):\n"
            sorted_cmds = sorted(command_usage.items(), key=lambda x: x[1].get("count", 0), reverse=True)
            popular = [(name, data) for name, data in sorted_cmds if data.get("count", 0) > 2]
            unused = [(name, data) for name, data in sorted_cmds if data.get("count", 0) <= 2]
            if popular:
                improvement_data += "Frequently used commands (create similar ones):\n"
                for name, data in popular[:5]:
                    improvement_data += f"- `!{name}`: Used {data['count']} times\n"
            if unused:
                improvement_data += "Rarely used commands (avoid creating similar ones):\n"
                for name, data in unused[:5]:
                    improvement_data += f"- `!{name}`: Used only {data.get('count', 0)} times\n"
        
        improvement_data += "\nUse this data to avoid repeating mistakes, prefer proven action sequences, and create commands users actually want."
        
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

MULTI-STEP CONVERSATIONS:
You can ask the user clarifying questions before executing. Use this when you need details like prices, names, or preferences.
When you need input, set "needs_input" to true and provide a "question" field. The user will reply and you'll get their response.

OUTPUT FORMAT:
You MUST output a valid JSON object with the following fields:
- "reasoning": Your deep chain-of-thought analysis (human-readable, bulleted).
- "walkthrough": A specific bulleted plan of action, ending with "Proceed?".
- "actions": A list of actions to perform. Each action has "name" and "parameters".
- "summary": A brief response to the user.
- "needs_input": Boolean. Set to true if you need more info from the user before executing.
- "question": If needs_input is true, the question to ask the user (e.g. "What items and prices should the shop have?").

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
