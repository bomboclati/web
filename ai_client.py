import os
import json
import aiohttp
import asyncio
from typing import List, Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential
from history_manager import history_manager
from vector_memory import vector_memory

class AIClient:
    """
    Handles deep reasoning, web searches, and JSON extraction.
    Ensures the bot thinks before acting and provides a walkthrough.
    Supports per-guild API keys.
    """
    def __init__(self, api_key: str, provider: str = "openrouter", model: Optional[str] = None):
        self.default_api_key = api_key
        self.default_provider = provider
        self.model = model or "meta-llama/llama-3.1-405b-instruct"
        self.base_urls = {
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        }

    def _get_guild_api_key(self, guild_id: int) -> tuple:
        """Get API key and provider for a specific guild, fallback to defaults"""
        from data_manager import dm
        guild_config = dm.get_guild_api_key(guild_id)
        if guild_config:
            return guild_config.get("api_key", self.default_api_key), guild_config.get("provider", self.default_provider)
        return self.default_api_key, self.default_provider

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
        Supports per-guild API keys.
        """
        # Get guild-specific API key (fallback to default)
        api_key, provider = self._get_guild_api_key(guild_id)
        
        if not api_key:
            return {"error": "No API key configured. Use /config apikey to set one."}
        
        # Get recent history
        history_depth = int(os.getenv("MEMORY_DEPTH", 20))
        history = history_manager.get_enhanced_context(guild_id, user_id, depth=history_depth)
        
        # Retrieve semantically similar conversations from vector memory
        vector_results = vector_memory.retrieve_relevant_conversations(
            guild_id=guild_id,
            user_id=user_id,
            query=user_input,
            n_results=int(os.getenv("VECTOR_MEMORY_RESULTS", 3)),
            min_importance=float(os.getenv("VECTOR_MEMORY_MIN_IMPORTANCE", 0.3))
        )
        
        # Format vector memory results as context messages
        vector_context = []
        for result in vector_results:
            doc = result["document"]
            similarity = result["similarity"]
            vector_context.append({
                "role": "system",
                "content": f"[Relevant past conversation (similarity: {similarity:.2f})]\n{doc}"
            })
        
        # Combine history and vector memory context
        combined_context = history + vector_context
        
        enhanced_prompt = self._build_enhanced_prompt(system_prompt, guild_id)
        
        # Build messages
        messages = [{"role": "system", "content": enhanced_prompt}]
        messages.extend(combined_context)
        messages.append({"role": "user", "content": user_input})

        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/antigravity"
                headers["X-Title"] = "Miro AI Discord Bot"

            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
            }
            if provider in ["openai", "openrouter"]:
                payload["response_format"] = {"type": "json_object"}

            async with session.post(self.base_urls.get(provider), headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"AI API Error ({resp.status}): {text}")
                
                data = await resp.json()
                ai_msg = data['choices'][0]['message']['content']

                # Try to parse JSON from AI message
                try:
                    res_json = json.loads(ai_msg)
                    
                    # Handle Web Search requested by AI
                    if res_json.get("action") == "web_search":
                        query = res_json.get("parameters", {}).get("query")
                        if query:
                            search_results = await self.get_search_results(query)
                            messages.append({"role": "assistant", "content": ai_msg})
                            messages.append({"role": "user", "content": f"Web Search Results for '{query}':\n{search_results}"})
                            
                            # Retry with search context
                            payload["messages"] = messages
                            async with session.post(self.base_urls.get(provider), headers=headers, json=payload) as search_resp:
                                search_data = await search_resp.json()
                                ai_msg = search_data['choices'][0]['message']['content']
                                res_json = json.loads(ai_msg)
                    
                    return res_json
                except json.JSONDecodeError:
                    raise Exception("AI failed to return valid JSON.")
        
        return {}

# Default System Prompt
SYSTEM_PROMPT = """
You are a creative, forward-thinking Discord bot AI with a continuous improvement mindset.
Every user request is an opportunity to deliver something super cool – beyond the bare minimum.

MANDATORY CLARIFICATION RULE:
If the user's request is vague or missing critical details (e.g., "build a shop" without items/prices), you MUST ask a clarifying question first.
NEVER guess or assume details for system creation. Always confirm with the user.
Set "needs_input": true and provide a specific "question" when you need details.

MANDATORY IMPLEMENTATION PLAN:
Before executing ANY action, you MUST provide a detailed "walkthrough" of what you will build.
The user will see this plan and must click "Proceed" to confirm.

MANDATORY AUTO-DOCUMENTATION RULE:
Whenever you create ANY system (channels, roles, commands, economy, tickets, verification, shop, appeals, staff, game, custom, etc.), you MUST:
1. Create a documentation channel named "<system-name>-guide"
2. Create '!' prefix commands for user interaction.
3. POST A COMPREHENSIVE DOCUMENTATION EMBED in that channel explaining:
   - System overview and purpose
   - ALL available commands with clear examples
   - How to use each feature step-by-step
   - Common troubleshooting tips
   - Who to contact for help
4. Send a quick start message showing the first command to try
5. ALWAYS create a '!help <systemname>' command that shows the documentation

Example help command format: "!help achievements" "!help shop" "!help tickets" (use SPACE between help and system name)

NEVER skip documentation. Even for simple systems, show users how to use it!

DOCUMENTATION FORMAT EXAMPLE:
When creating documentation, use channel name format: "system-name-guide" (lowercase, hyphens)
{
  "name": "send_embed",
  "parameters": {
    "channel": "role-shop-guide",
    "title": "🛍️ Role Shop System",
    "description": "Welcome to the Role Shop! Purchase exclusive roles using your coins.",
    "color": "gold",
    "fields": [
      {
        "name": "📋 Available Commands",
        "value": "!buy <role_name> - Purchase a role\n!balance - Check your coins\n!daily - Claim daily coins",
        "inline": false
      },
      {
        "name": "🚀 Getting Started",
        "value": "1. Use !daily to get 100 coins\n2. Use !balance to check funds\n3. Use !buy <role> to purchase",
        "inline": false
      },
      {
        "name": "❓ Troubleshooting",
        "value": "• Not enough coins? Use !daily\n• Command not working? Check spelling\n• Need help? Contact an admin",
        "inline": false
      }
    ],
    "footer": "Created by Miro AI • Use !help shop for this guide"
  }
}

ACTION EXAMPLES:
- {"name": "create_channel", "parameters": {"name": "shop", "type": "text", "category": "Economy"}}
- {"name": "create_prefix_command", "parameters": {"name": "buy", "code": "..."}}
- {"name": "send_embed", "parameters": {"channel": "...", "title": "...", "description": "..."}}
- {"name": "web_search", "parameters": {"query": "..."}}
- {"name": "schedule_ai_action", "parameters": {"name": "daily_welcome", "cron": "0 9 * * *", "action_type": "announcement", "action_params": {"title": "Good Morning!", "message": "Start your day with positivity!"}, "channel_id": 123456789}}

SCHEDULED ACTIONS:
You can schedule actions to run automatically using cron expressions:
- Use action_type "announcement" to send scheduled messages to a channel
- Use action_type "reminder" to remind users at specific times
- Use action_type "ai_action" to have the AI perform actions on a schedule
- Cron format: "minute hour day month weekday" (e.g., "0 9 * * *" = 9 AM daily)
- Get channel_id from the server configuration or ask the user

IMMORTAL GUARANTEE:
Maintain state in data/ JSON files. Never lose a single bit of information.
"""
