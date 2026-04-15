import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from data_manager import dm
from logger import logger
from history_manager import history_manager
from vector_memory import vector_memory


class ChannelMode(Enum):
    GENERAL = "general"
    HELP = "help"
    RPG = "rpg"
    COUNSELOR = "counselor"
    TRANSLATOR = "translator"
    CUSTOM = "custom"
    CODING = "coding"
    CREATIVE = "creative"
    GAMING = "gaming"


class AIProvider(Enum):
    DEFAULT = "default"
    CLAUDE = "claude"
    GPT4 = "gpt4"
    DEEPSEEK = "deepseek"
    LOCAL = "local"


@dataclass
class AIChatChannel:
    id: str
    guild_id: int
    channel_id: int
    mode: ChannelMode
    persona: str
    system_prompt: str
    memory_depth: int
    translate_languages: List[str]
    custom_settings: dict
    created_at: float
    created_by: int


class AIChatSystem:
    def __init__(self, bot):
        self.bot = bot
        self._chat_channels: Dict[str, AIChatChannel] = {}
        self._channel_sessions: Dict[int, dict] = {}
        self._load_channels()

    def _load_channels(self):
        data = dm.load_json("ai_chat_channels", default={})
        
        for channel_id, c_data in data.items():
            try:
                channel = AIChatChannel(
                    id=channel_id,
                    guild_id=c_data["guild_id"],
                    channel_id=c_data["channel_id"],
                    mode=ChannelMode(c_data["mode"]),
                    persona=c_data.get("persona", ""),
                    system_prompt=c_data.get("system_prompt", ""),
                    memory_depth=c_data.get("memory_depth", 10),
                    translate_languages=c_data.get("translate_languages", []),
                    custom_settings=c_data.get("custom_settings", {}),
                    created_at=c_data["created_at"],
                    created_by=c_data["created_by"]
                )
                self._chat_channels[channel_id] = channel
            except Exception as e:
                logger.error(f"Failed to load AI chat channel {channel_id}: {e}")

    def _save_channel(self, channel: AIChatChannel):
        data = dm.load_json("ai_chat_channels", default={})
        data[channel.id] = {
            "guild_id": channel.guild_id,
            "channel_id": channel.channel_id,
            "mode": channel.mode.value,
            "persona": channel.persona,
            "system_prompt": channel.system_prompt,
            "memory_depth": channel.memory_depth,
            "translate_languages": channel.translate_languages,
            "custom_settings": channel.custom_settings,
            "created_at": channel.created_at,
            "created_by": channel.created_by
        }
        dm.save_json("ai_chat_channels", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "ai_chat_settings", {
            "enabled": True,
            "default_memory_depth": 10,
            "max_channels": 5,
            "allowed_modes": ["general", "help", "rpg", "counselor", "translator"]
        })

    def _get_default_persona(self, mode: ChannelMode) -> tuple:
        personas = {
            ChannelMode.GENERAL: (
                "Friendly AI Assistant",
                "You are a friendly, helpful AI assistant in a Discord server. Be conversational, helpful, and engaging. Keep responses concise but informative."
            ),
            ChannelMode.HELP: (
                "Tech Support",
                "You are a technical support AI. Help users with their problems, ask clarifying questions, and provide step-by-step solutions. Be patient and thorough."
            ),
            ChannelMode.RPG: (
                "Fantasy Narrator",
                "You are a fantasy RPG AI narrator. Create immersive story experiences. Respond to player actions, describe scenes, and drive the narrative forward. Be creative and descriptive."
            ),
            ChannelMode.COUNSELOR: (
                "Supportive Counselor",
                "You are a supportive, empathetic counselor AI. Listen attentively, validate feelings, and provide gentle guidance. Never give medical advice. Be warm and understanding."
            ),
            ChannelMode.TRANSLATOR: (
                "Language Translator",
                "You are a multilingual translator. Translate messages between languages accurately. Detect the source language and respond in the requested language."
            ),
            ChannelMode.CUSTOM: (
                "Custom AI",
                "You are a helpful AI assistant. Be friendly and engage in conversation."
            )
        }
        return personas.get(mode, personas[ChannelMode.GENERAL])

    async def create_chat_channel(self, guild_id: int, channel_id: int, mode: ChannelMode,
                                custom_persona: str = None, custom_prompt: str = None,
                                created_by: int = 0) -> AIChatChannel:
        channel_id_str = str(channel_id)
        
        if channel_id_str in self._chat_channels:
            return self._chat_channels[channel_id_str]
        
        persona, system_prompt = self._get_default_persona(mode)
        
        if custom_persona:
            persona = custom_persona
        if custom_prompt:
            system_prompt = custom_prompt
        
        settings = self.get_guild_settings(guild_id)
        
        chat_channel = AIChatChannel(
            id=channel_id_str,
            guild_id=guild_id,
            channel_id=channel_id,
            mode=mode,
            persona=persona,
            system_prompt=system_prompt,
            memory_depth=settings.get("default_memory_depth", 10),
            translate_languages=[],
            custom_settings={},
            created_at=time.time(),
            created_by=created_by
        )
        
        self._chat_channels[channel_id_str] = chat_channel
        self._save_channel(chat_channel)
        
        return chat_channel

    async def handle_message(self, message: discord.Message) -> Optional[discord.Message]:
        if message.author.bot:
            return None
        
        channel_id_str = str(message.channel.id)
        
        if channel_id_str not in self._chat_channels:
            return None
        
        chat_channel = self._chat_channels[channel_id_str]
        
        if chat_channel.mode == ChannelMode.TRANSLATOR:
            return await self._handle_translator_mode(message, chat_channel)
        
        return await self._handle_ai_chat(message, chat_channel)

    async def _handle_ai_chat(self, message: discord.Message, chat_channel: AIChatChannel) -> Optional[discord.Message]:
        user_input = message.content
        
        session_key = f"{message.guild.id}_{message.author.id}_{message.channel.id}"
        
        if session_key not in self._channel_sessions:
            self._channel_sessions[session_key] = {
                "messages": [],
                "started_at": time.time()
            }
        
        session = self._channel_sessions[session_key]
        
        history = history_manager.get_enhanced_context(
            message.guild.id,
            message.author.id,
            depth=chat_channel.memory_depth
        )
        
        if chat_channel.mode == ChannelMode.RPG:
            rpg_context = await self._get_rpg_context(message.guild.id)
            system_prompt = chat_channel.system_prompt + "\n\n" + rpg_context
        else:
            system_prompt = chat_channel.system_prompt
        
        try:
            # Check for AI provider override from channel settings
            provider = getattr(chat_channel, 'ai_provider', None)
            
            if provider and provider != AIProvider.DEFAULT:
                # Use different AI for this channel
                result = await self._chat_with_provider(
                    message.guild.id,
                    message.author.id,
                    user_input,
                    system_prompt,
                    provider
                )
            else:
                result = await self.bot.ai.chat(
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    user_input=user_input,
                    system_prompt=system_prompt
                )
            
            # Use actual raw response directly instead of summary field
            response = result.get("content", result.get("summary", "I didn't quite catch that. Could you try again?"))
            # Strip any summary headers/footers
            import re
            response = re.sub(r'^(?:Summary|Response):\s*', '', response, flags=re.IGNORECASE)
            response = re.sub(r'\s*---\s*.*$', '', response, flags=re.DOTALL)
            response = re.sub(r'\s*\*\*Summary\*\*:.*$', '', response, flags=re.IGNORECASE|re.DOTALL)
            # Remove ALL markdown formatting, embeds, cards, attachments
            response = re.sub(r'\*\*(.*?)\*\*', r'\1', response)
            response = re.sub(r'\*(.*?)\*', r'\1', response)
            response = re.sub(r'__(.*?)__', r'\1', response)
            response = re.sub(r'`(.*?)`', r'\1', response)
            response = re.sub(r'```.*?```', '', response, flags=re.DOTALL)
            response = re.sub(r'\[.*?\]\(.*?\)', '', response)
            response = re.sub(r'<@!?\d+>', '', response)
            response = re.sub(r'<#\d+>', '', response)
            response = re.sub(r'<@&\d+>', '', response)
            response = re.sub(r'https?://\S+', '', response)
            response = response.strip()
            
            session["messages"].append({"role": "user", "content": user_input})
            session["messages"].append({"role": "assistant", "content": response})
            
            if len(session["messages"]) > 50:
                session["messages"] = session["messages"][-50:]
            
            vector_memory.store_conversation(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_message=user_input,
                bot_response=response,
                reasoning=result.get("reasoning", ""),
                walkthrough=result.get("walkthrough", ""),
                importance_score=0.5
            )
            
            if len(response) > 2000:
                response = response[:1997] + "..."
            
            # Absolutely NO embeds, NO rich formatting, NO extras - just plain text
            return await message.channel.send(response, suppress_embeds=True, embeds=[], files=[], attachments=[])
            
        except Exception as e:
            logger.error(f"AI chat error: {e}")
            return await message.channel.send("Sorry, I encountered an error. Please try again.", suppress_embeds=True)

    async def _handle_translator_mode(self, message: discord.Message, chat_channel: AIChatChannel) -> Optional[discord.Message]:
        user_input = message.content
        
        prompt = f"""Translate this message. Detect the source language and translate to all configured languages.

AVAILABLE LANGUAGES: {', '.join(chat_channel.translate_languages)}

MESSAGE TO TRANSLATE: {user_input}

Respond with JSON only:
{{
    "detected_language": "language name",
    "translations": {{
        "language1": "translated text",
        "language2": "translated text"
    }}
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_input=prompt,
                system_prompt="You are a multilingual translator. Translate accurately and preserve meaning."
            )
            
            translations = result.get("translations", {})
            detected = result.get("detected_language", "Unknown")
            
            embed = discord.Embed(
                title="🌐 Translation",
                description=f"Detected: **{detected}**",
                color=discord.Color.blue()
            )
            
            for lang, text in translations.items():
                embed.add_field(name=lang.title(), value=text, inline=False)
            
            return await message.channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return await message.channel.send("Sorry, translation failed. Please try again.", suppress_embeds=True)

    async def _get_rpg_context(self, guild_id: int) -> str:
        rpg_data = dm.get_guild_data(guild_id, "rpg_data", {})
        
        if not rpg_data:
            return "This is a new adventure. The world is waiting to be explored."
        
        context = "RECENT ADVENTURE:\n"
        
        for key, value in rpg_data.items():
            if key.startswith("story_"):
                context += f"- {value[:200]}\n"
        
        return context
    
    async def _chat_with_provider(self, guild_id: int, user_id: int, user_input: str, 
                                system_prompt: str, provider: AIProvider) -> dict:
        """Multi-AI Provider System - Chat with a specific AI provider."""
        try:
            if provider == AIProvider.CLAUDE:
                # Use Claude via existing AI client
                return await self.bot.ai.chat(guild_id, user_id, user_input, system_prompt)
            elif provider == AIProvider.GPT4:
                return await self.bot.ai.chat(guild_id, user_id, user_input, system_prompt)
            elif provider == AIProvider.DEEPSEEK:
                return await self.bot.ai.chat(guild_id, user_id, user_input, system_prompt)
            else:
                return await self.bot.ai.chat(guild_id, user_id, user_input, system_prompt)
        except Exception as e:
            logger.error(f"AI provider error: {e}")
            return {"summary": "Sorry, AI service temporarily unavailable."}
    
    async def _handle_web_search(self, user_input: str, system_prompt: str) -> str:
        """Web Search System - Search the web and include results in AI response."""
        try:
            search_results = await self.bot.ai.get_search_results(user_input)
            
            if not search_results or "disabled" in search_results.lower() or "error" in search_results.lower():
                return None
            
            enhanced_prompt = f"{system_prompt}\n\nWEB SEARCH RESULTS:\n{search_results}\n\nBased on these results, answer the user's question."
            
            result = await self.bot.ai.chat(
                guild_id=0,
                user_id=0,
                user_input=user_input,
                system_prompt=enhanced_prompt
            )
            
            return result.get("summary")
        except Exception as e:
            logger.error(f"Web search error: {e}")
            return None
    
    """AI Command Execution"""
    async def _check_for_commands(self, message: discord.Message, response: str) -> Optional[str]:
        """Check if AI wants to execute a command."""
        if not response.startswith("!") and not response.startswith("/"):
            return None
        
        # Sanitize command
        cmd = response.strip().split()[0]
        allowed_cmds = {"ping", "server", "userinfo", "avatar", "botinfo"}
        
        # Check if allowed
        if cmd.lstrip("!/") in allowed_cmds:
            return response
        
        return None
    
    async def update_channel_settings(self, channel_id: str, **kwargs):
        if channel_id not in self._chat_channels:
            return
        
        chat_channel = self._chat_channels[channel_id]
        
        for key, value in kwargs.items():
            if hasattr(chat_channel, key):
                setattr(chat_channel, key, value)
        
        self._save_channel(chat_channel)

    def get_channel_info(self, channel_id: str) -> Optional[dict]:
        if channel_id not in self._chat_channels:
            return None
        
        chat_channel = self._chat_channels[channel_id]
        
        return {
            "id": chat_channel.id,
            "mode": chat_channel.mode.value,
            "persona": chat_channel.persona,
            "memory_depth": chat_channel.memory_depth,
            "translate_languages": chat_channel.translate_languages
        }

    def list_guild_channels(self, guild_id: int) -> List[AIChatChannel]:
        return [ch for ch in self._chat_channels.values() if ch.guild_id == guild_id]

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "ai_chat_settings", settings)
        
        help_embed = discord.Embed(
            title="💬 AI Chat Channels",
            description="Dedicated AI conversation channels with personas and channel-specific memories.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Create AI-powered text channels. Each channel can have its own persona (friendly, help, RPG, counselor, translator). The AI remembers conversations in that channel.",
            inline=False
        )
        help_embed.add_field(
            name="Channel Modes",
            value="• **general** - Friendly conversational AI\n• **help** - Technical support\n• **rpg** - Fantasy storytelling\n• **counselor** - Supportive listener\n• **translator** - Multi-language",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["help aichat"] = json.dumps({
            "command_type": "help_embed",
            "title": "💬 AI Chat Channels",
            "description": "Dedicated AI conversation channels.",
            "fields": [
                {"name": "How it works", "value": "Each channel has its own AI persona with channel-specific memory.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
