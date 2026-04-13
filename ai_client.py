import os
import json
import aiohttp
import logging
import re
import asyncio
from typing import List, Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, retry_if_not_exception_type
from history_manager import history_manager
from vector_memory import vector_memory

logger = logging.getLogger(__name__)

class AIClientError(Exception):
    """Custom exception for AI client errors that should not be retried (e.g., 4xx)."""
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"AI API Client Error ({status}): {message}")

def is_retryable_exception(exception):
    """
    Only retry on server errors (5xx) or connection issues.
    Skip client errors (4xx) and logic/parsing errors (KeyError, etc).
    """
    if isinstance(exception, AIClientError):
        return exception.status >= 500 or exception.status == 429
    
    # Don't retry on structural or logic errors - these are bugs, not transient issues
    if isinstance(exception, (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError, ValueError)):
        return False
        
    return True

class AIClient:
    """
    Handles deep reasoning, web searches, and JSON extraction.
    Ensures the bot thinks before acting and provides a walkthrough.
    Supports per-guild API keys.
    """
    def __init__(self, api_key: str, provider: str = None, model: Optional[str] = None):
        self.default_api_key = api_key
        self.default_provider = provider or os.getenv("AI_PROVIDER", "openrouter")
        # Default model logic
        self.model = model or os.getenv("AI_MODEL", "openai/gpt-3.5-turbo")
        
        self.base_urls = {
            "openrouter": os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"),
            "openai": os.getenv("OPENAI_URL", "https://api.openai.com/v1/chat/completions"),
            "gemini": os.getenv("GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
            "anthropic": os.getenv("ANTHROPIC_URL", "https://api.anthropic.com/v1/messages"),
            "groq": os.getenv("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"),
            "mistral": os.getenv("MISTRAL_URL", "https://api.mistral.ai/v1/chat/completions"),
            "deepseek": os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions"),
            "dashscope": os.getenv("DASHSCOPE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
            "qwen": os.getenv("DASHSCOPE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
            "cerebras": os.getenv("CEREBRAS_URL", "https://api.cerebras.ai/v1/chat/completions"),
            "sambanova": os.getenv("SAMBANOVA_URL", "https://api.sambanova.ai/v1/chat/completions"),
            "together": os.getenv("TOGETHER_URL", "https://api.together.xyz/v1/chat/completions")
        }

    def _get_guild_api_key(self, guild_id: int) -> tuple:
        """Get API key and provider for a specific guild, fallback to defaults"""
        from data_manager import dm
        # Get active provider for the guild
        current_config = dm.get_guild_api_key(guild_id)
        if current_config:
            return current_config.get("api_key", self.default_api_key), current_config.get("provider", self.default_provider)
        return self.default_api_key, self.default_provider

    def _get_all_guild_keys(self, guild_id: int) -> List[Dict[str, str]]:
        """Fetch all available API keys for a guild to use as fallbacks"""
        from data_manager import dm
        api_keys = dm.load_json("guild_api_keys", default={})
        guild_data = api_keys.get(str(guild_id), {})
        
        def is_valid_key(k):
            return k and len(k) > 10 and not any(x in k.upper() for x in ["YOUR_", "REPLACE_"])

        results = []
        # Add primary first
        primary = self._get_guild_api_key(guild_id)
        if is_valid_key(primary[0]):
            results.append({"api_key": primary[0], "provider": primary[1]})
        
        # Add others if available
        providers = guild_data.get("providers", {})
        if isinstance(providers, dict):
            for p, enc_key in providers.items():
                if p != primary[1]:
                    # Decrypt and add
                    res = dm.get_guild_api_key(guild_id, provider=p)
                    if res and is_valid_key(res.get("api_key")):
                        results.append(res)
        
        # Finally add defaults if not already present and valid
        if is_valid_key(self.default_api_key) and not any(r["provider"] == self.default_provider for r in results):
            results.append({"api_key": self.default_api_key, "provider": self.default_provider})
            
        return results
    
    async def fetch_server_health(self, guild_id: int) -> Dict[str, Any]:
        """
        Tool function to fetch server health and forecast data.
        Called by AI when user asks about server activity, forecasts, or level-up ETAs.
        """
        try:
            from modules.server_analytics import get_analytics
            analytics = get_analytics()
            if analytics:
                return analytics.get_forecast(guild_id)
            return {"error": "Analytics system not initialized"}
        except Exception as e:
            return {"error": str(e)}

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

    @retry(
        retry=retry_if_exception(is_retryable_exception),
        stop=stop_after_attempt(5), 
        wait=wait_exponential(multiplier=2, min=2, max=60)
    )
    async def chat(self, guild_id: int, user_id: int, user_input: str, system_prompt: str) -> Dict[str, Any]:
        """
        Communicates with the LLM using a primary provider, with automatic fallback
        to secondary providers (DashScope, OpenRouter) if the primary hits a quota limit (429/403).
        """
        keys_to_try = self._get_all_guild_keys(guild_id)
        if not keys_to_try:
            logger.error(f"[AI ERROR] No API keys configured for guild {guild_id}")
            return {"error": "No valid API key configured. Use /config apikey to set one."}

        last_error = None

        for key_bundle in keys_to_try:
            api_key = key_bundle["api_key"]
            provider = key_bundle["provider"]
            
            try:
                return await self._chat_internal(guild_id, user_id, user_input, system_prompt, api_key, provider)
            except AIClientError as e:
                # If it's a quota (429) or access (403) error, try the next provider in the guild's list
                if e.status in [429, 403]:
                    logger.warning(f"[AI FALLBACK] Provider {provider} failed with {e.status}. Trying next available fallback...")
                    last_error = e
                    continue
                raise # Re-raise server/connection errors for the 'tenacity' @retry to handle
            except Exception as e:
                # Re-raise transient exceptions for tenacity to handle
                raise

        if last_error:
            raise last_error
        
        raise Exception("AI failed to respond after trying all configured fallback providers.")

    async def _chat_internal(self, guild_id: int, user_id: int, user_input: str, system_prompt: str, api_key: str, provider: str) -> Dict[str, Any]:
        """Internal execution for a single AI provider request."""
        # Get recent history
        history_depth = int(os.getenv("MEMORY_DEPTH", 20))
        history = await history_manager.get_enhanced_context(guild_id, user_id, depth=history_depth)
        if not isinstance(history, list):
            history = []
        
        # Retrieve semantically similar conversations from vector memory
        vector_results = await vector_memory.retrieve_relevant_conversations(
            guild_id=guild_id,
            user_id=user_id,
            query=user_input,
            n_results=int(os.getenv("VECTOR_MEMORY_RESULTS", 3)),
            min_importance=float(os.getenv("VECTOR_MEMORY_MIN_IMPORTANCE", 0.3))
        )
        
        vector_context = []
        for result in vector_results:
            doc = result.get("document", "")
            similarity = result.get("similarity")
            if similarity is None:
                distance = result.get("distance", 1.0)
                similarity = 1.0 - distance
            
            vector_context.append({
                "role": "system",
                "content": f"[Relevant past conversation (similarity: {similarity:.2f})]\n{doc}"
            })
        
        combined_context = history + vector_context
        enhanced_prompt = self._build_enhanced_prompt(system_prompt, guild_id)
        
        messages = [{"role": "system", "content": enhanced_prompt}]
        messages.extend(combined_context)
        messages.append({"role": "user", "content": user_input})
        
        # Determine model based on provider
        from data_manager import dm
        active_model = dm.get_guild_data(guild_id, "custom_model", self.model)
        
        if provider == "gemini" and (not active_model or "gpt" in active_model.lower()):
            active_model = "gemini-1.5-flash-latest"
        elif provider == "openai" and (not active_model or "/" in active_model):
            active_model = "gpt-3.5-turbo"
        elif provider == "anthropic" and (not active_model or "gpt" in active_model.lower()):
            active_model = "claude-3-5-sonnet-20240620"
        elif provider == "cerebras" and (not active_model or "gpt" in active_model.lower() or "claude" in active_model.lower()):
            active_model = "llama3.3-70b"
        elif provider == "sambanova" and (not active_model or "gpt" in active_model.lower()):
            active_model = "llama3.1-70b-instruct"
        elif provider == "groq" and (not active_model or "gpt" in active_model.lower() or "/" in active_model):
            active_model = "llama-3.3-70b-versatile"
        elif provider == "together" and (not active_model or "gpt" in active_model.lower()):
            active_model = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        elif provider in ["qwen", "dashscope"] and (not active_model or "gpt" in active_model.lower()):
            active_model = "qwen2.5-72b-instruct" # Updated to newest stable qwen
        elif not active_model:
            active_model = self.model or "gpt-3.5-turbo"
        
        if provider == "anthropic":
            headers = {
                "x-api-key": api_key.strip(),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
        else:
            headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json"
            }
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/antigravity"
                headers["X-Title"] = "Miro AI Discord Bot"

        logger.info(f"AI Handshake: {provider} | Model: {active_model}")

        payload = {
            "model": active_model,
            "messages": [m for m in messages if m["role"] != "system"],
            "temperature": 0.7,
        }
        
        # Anthropic specific payload structure
        if provider == "anthropic":
            system_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
            if system_msg:
                payload["system"] = system_msg
            payload["max_tokens"] = 4096
        else:
            # Add system message back for OpenAI compatible providers
            payload["messages"] = messages
            # Only force JSON response format if prompt contains 'json' (required by most providers)
            prompt_has_json = 'json' in system_prompt.lower() or any('json' in str(m.get('content','')).lower() for m in messages[:3])
            if provider in ["openai", "openrouter", "gemini", "groq", "mistral", "deepseek", "qwen", "dashscope", "cerebras", "sambanova", "together"] and prompt_has_json:
                payload["response_format"] = {"type": "json_object"}

        timeout = aiohttp.ClientTimeout(total=45, connect=10)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            provider_url = self.base_urls.get(provider)
            if not provider_url:
                raise Exception(f"Unsupported AI provider: {provider}")
            
            # Gemini specific URL handling (expects key in query param)
            if provider == "gemini":
                if "?" in provider_url:
                    provider_url += f"&key={api_key.strip()}"
                else:
                    provider_url += f"?key={api_key.strip()}"

            logger.info(f"AI Handshake Executing: {provider} | URL: {provider_url.split('?')[0]}")

            async with session.post(provider_url, json=payload, allow_redirects=False) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"AI API Error from {provider} ({resp.status}): {text}")
                    
                    # One-provider fallback for 403 on flagship models
                    if resp.status == 403 and ("Unpurchased" in text or "denied" in text.lower()) and "turbo" not in active_model:
                        fallback = "qwen-turbo" if provider in ["qwen", "dashscope"] else "gpt-3.5-turbo"
                        logger.warning(f"Access Denied for {active_model}. Falling back to {fallback}...")
                        payload["model"] = fallback
                        async with session.post(provider_url, json=payload, allow_redirects=False) as fallback_resp:
                            if fallback_resp.status == 200:
                                return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, fallback_resp)
                    
                    raise AIClientError(resp.status, text)
                
                return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, resp)

    async def _parse_and_handle_response(self, session, provider, provider_url, payload, messages, resp) -> dict:
        """Parses the AI response and handles any requested actions (like web search)."""
        res_data = await resp.json()
        
        # Flexible extraction for different provider formats
        try:
            if 'choices' in res_data:
                ai_msg = res_data['choices'][0]['message']['content']
            elif 'content' in res_data:
                ai_msg = res_data['content'][0]['text']
            elif 'message' in res_data and 'content' in res_data['message']:
                ai_msg = res_data['message']['content']
            else:
                logger.error(f"Unknown response structure from {provider}: {res_data}")
                raise KeyError(f"No valid 'choices' or 'content' in response from {provider}")
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to parse {provider} response: {e}\nData: {res_data}")
            raise

        logger.debug(f"AI Handshake Successful | Provider: {provider} | Response Length: {len(ai_msg)}")

        # Try to parse JSON from AI message
        try:
            res_json = self._extract_json(ai_msg)
            
            # Handle Web Search requested by AI
            if res_json.get("action") == "web_search":
                query = res_json.get("parameters", {}).get("query")
                if query:
                    search_results = await self.get_search_results(query)
                    messages.append({"role": "assistant", "content": ai_msg})
                    messages.append({"role": "user", "content": f"Web Search Results for '{query}':\n{search_results}"})
                    
                    payload["messages"] = messages
                    async with session.post(provider_url, json=payload, allow_redirects=False) as search_resp:
                        if search_resp.status == 200:
                            return await self._parse_and_handle_response(session, provider, provider_url, payload, messages, search_resp)
                        else:
                            search_text = await search_resp.text()
                            logger.error(f"AI Search Retry Error ({search_resp.status}): {search_text}")
                            raise Exception(f"AI Search API Error ({search_resp.status})")
            
            return res_json
        except Exception as e:
            logger.debug(f"Response was not pure JSON or parse failed: {e}")
            return {"response": ai_msg, "reasoning": "Standard response", "summary": ai_msg}


    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Robustly extract JSON from AI response, handling Markdown and conversational filler."""
        if not text:
            return {}
            
        # Try direct parse first
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
            
        # Try to find JSON block in markdown
        json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            try:
                # Remove possible trailing commas before closing braces/brackets
                content = re.sub(r',\s*([\]}])', r'\1', match.group(1))
                return json.loads(content)
            except json.JSONDecodeError:
                pass
                
        # Try to find anything that looks like a JSON object using basic brace matching
        brace_pattern = r"(\{.*\})"
        match = re.search(brace_pattern, text, re.DOTALL)
        if match:
            try:
                # Basic cleanup: remove everything before first { and after last }
                content = match.group(1)
                # Remove possible trailing commas
                content = re.sub(r',\s*([\]}])', r'\1', content)
                return json.loads(content)
            except json.JSONDecodeError:
                pass
                
        # Final desperate attempt: find first { and last } manually
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1:
                content = text[start:end+1]
                content = re.sub(r',\s*([\]}])', r'\1', content)
                return json.loads(content)
        except Exception:
            pass
                
        # Cannot parse as JSON - return the raw text as the summary
        logger.warning(f"Could not parse AI response as JSON, using raw text. Preview: {text[:100]}")
        return {"summary": text.strip(), "reasoning": "Raw text response", "walkthrough": ""}

# Default System Prompt
SYSTEM_PROMPT = """
You are a creative, proactive Discord bot AI that takes immediate action to build cool features.
Every user request is an opportunity to deliver something awesome - go beyond the bare minimum.

MANDATORY JSON FORMAT:
You MUST ALWAYS respond with a JSON object containing these keys:
1. "reasoning": (string) Your internal thoughts and plan.
2. "summary": (string) Your friendly response to the user. MANDATORY.
3. "walkthrough": (string) Detailed overview of what you will build. Required for actions.
4. "action": (string|null) The name of the tool/action to perform.
5. "parameters": (dict|null) Parameters for the action.

ACTION-FIRST APPROACH:
- By DEFAULT, set "needs_input": false and proceed with reasonable defaults
- Only set "needs_input": true if you absolutely CANNOT proceed without specific user input
- When building systems, make sensible assumptions (e.g., default colors, common channel names)
- Create complete, working systems immediately without asking for confirmation on every detail

MANDATORY IMPLEMENTATION PLAN:
Before executing ANY action, provide a detailed "walkthrough" of what you will build.
The system will execute your actions automatically when needs_input is false.

MANDATORY AUTO-DOCUMENTATION RULE:
Whenever you create ANY system (channels, roles, commands, economy, tickets, verification, shop, etc.), you MUST:
1. Create a documentation channel named "<system-name>-guide"
2. Create '!' prefix commands for user interaction
3. POST COMPREHENSIVE DOCUMENTATION in that channel with:
   - System overview and purpose
   - ALL available commands with examples
   - Step-by-step usage instructions
   - Troubleshooting tips
   - Support contact info
4. Send a quick start message showing the first command to try
5. ALWAYS create a '!help <systemname>' command

Example: "!help shop", "!help tickets", "!help achievements"

NEVER skip documentation. Show users how to use every feature you create!

[IMPORTANT: INTERACTION RELIABILITY]
- All buttons must be functional and connected to real event handlers
- Use persistent views with timeout=None for buttons that need to work after restarts
- For auto-setup buttons (Verify, Rules, Tickets, Applications, Roles), use the classes from modules/auto_setup.py
- Buttons must have unique custom_ids and proper callback methods
- Never send embeds with placeholder buttons that don't work

ACTION EXAMPLES:
- {"name": "create_channel", "parameters": {"name": "shop", "type": "text", "category": "Economy"}}
- {"name": "create_prefix_command", "parameters": {"name": "buy", "code": "{\"command_type\": \"simple\", \"content\": \"Welcome!\"}"}}
- {"name": "send_embed", "parameters": {"channel": "verify", "title": "Verify", "description": "Click to verify!", "buttons": [{"label": "Verify Me", "type": "verify", "style": "success"}]}}
- {"name": "send_embed", "parameters": {"channel": "rules", "title": "Rules", "description": "Read!", "buttons": [{"label": "I Accept", "type": "accept_rules"}], "fields": [{"name": "Rule 1", "value": "Be respectful", "inline": false}]}}
- {"name": "send_embed", "parameters": {"channel": "tickets", "title": "Support", "description": "Need help?", "buttons": [{"label": "Open Ticket", "type": "ticket"}]}}
- {"name": "send_embed", "parameters": {"channel": "staff-apps", "title": "Staff Apply", "description": "Join our team!", "buttons": [{"label": "Apply Now", "type": "apply_staff"}]}}
- {"name": "web_search", "parameters": {"query": "..."}}
- {"name": "assign_role", "parameters": {"role_name": "Bots", "username": "john"}}
- {"name": "assign_role", "parameters": {"role_name": "Member", "user_id": "123456789"}}
- {"name": "schedule_ai_action", "parameters": {"name": "daily_welcome", "cron": "0 9 * * *", "action_type": "announcement", "action_params": {"title": "Good Morning!", "message": "Start your day with positivity!"}, "channel_id": 123456789}}
- {"name": "send_dm", "parameters": {"username": "john", "content": "Hello!"}}
- {"name": "ping", "parameters": {"username": "john"}}
- {"name": "create_invite", "parameters": {"channel": "general"}}
- {"name": "kick_user", "parameters": {"username": "john", "reason": "Rule violation"}}
- {"name": "ban_user", "parameters": {"username": "john", "reason": "Spamming"}}
- {"name": "timeout_user", "parameters": {"username": "john", "duration": 600, "reason": "Time out for 10 mins"}}

WHEN TO USE PING vs SEND_DM:
- Use "ping" when user wants to mention/ping "@user" or check their status - shows in CHANNEL
- Use "send_dm" when user wants to privately message someone - sends to their DM

ROLE ASSIGNMENT RULES:
- To create a role AND give it to a user, use TWO actions in sequence:
  1. {"name": "create_role", "parameters": {"name": "Bots", "color": "#99AAB5"}}
  2. {"name": "assign_role", "parameters": {"role_name": "Bots", "username": "john"}}
- ALWAYS use "role_name" (the role's text name) and "username" (the user's display name, without @) in assign_role parameters
- NEVER use role_id or numeric user_id — the bot will resolve names automatically
- When the user mentions someone with @, extract just their username for the "username" field

BUTTON TYPES for send_embed (all fully functional, no placeholders):
- "verify" = Gives user Verified/Member role automatically
- "ticket" = Creates a support ticket thread
- "accept_rules" = Accepts rules and grants Verified role
- "apply_staff" = Opens a staff application form
- "suggestion" = Prompts user to submit a suggestion
- "custom" = Shows a custom response (add "response": "message" field)

PREFIX COMMAND code MUST be valid JSON string. Formats:
- Simple text: {"command_type": "simple", "content": "Your message"}
- Help embed: {"command_type": "help_embed", "title": "Help", "commands": [{"name": "!cmd", "description": "What it does"}]}
- Economy daily: {"command_type": "economy_daily"}
- Balance: {"command_type": "economy_balance"}
- Show all commands: {"command_type": "help_all"}

SCHEDULED ACTIONS:
Schedule automatic actions using cron expressions:
- action_type "announcement" for scheduled messages
- action_type "reminder" for user reminders  
- action_type "ai_action" for AI-performed tasks
- Cron format: "minute hour day month weekday" (e.g., "0 9 * * *" = 9 AM daily)

IMMORTAL GUARANTEE:
Maintain state in data/ JSON files. Never lose information.
"""
