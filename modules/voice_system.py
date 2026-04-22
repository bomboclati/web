import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger
import os


@dataclass
class VoiceSession:
    user_id: int
    guild_id: int
    channel_id: int
    started_at: float
    xp_earned: int
    coins_earned: int
    messages_count: int


class VoiceActivitySystem:
    def __init__(self, bot):
        self.bot = bot
        self._voice_sessions: Dict[int, VoiceSession] = {}
        self._voice_channels: Dict[int, dict] = {}
        self._leaderboards: Dict[int, List[dict]] = {}

    def _load_guild_data(self, guild_id: int):
        """Lazy load guild data to ensure multi-server isolation."""
        if guild_id not in self._voice_channels:
            data = dm.get_guild_data(guild_id, "voice_system_data", {})
            self._voice_channels[guild_id] = data.get("channels", {})

    def _save_guild_data(self, guild_id: int):
        """Save guild data immediately for immortality."""
        data = {
            "channels": self._voice_channels.get(guild_id, {})
        }
        dm.update_guild_data(guild_id, "voice_system_data", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "voice_settings", {
            "enabled": True,
            "xp_per_minute": 5,
            "coins_per_minute": 2,
            "bonus_xp_threshold": 30,
            "bonus_coins_threshold": 30,
            "voice_roles": {},
            "auto_afk": True,
            "afk_timeout_minutes": 5,
            "highlight_recording": False
        })

    def start_voice_monitoring(self):
        asyncio.create_task(self._voice_monitor_loop())

    async def _voice_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    self._load_guild_data(guild.id)
                    for voice_channel in guild.voice_channels:
                        for member in voice_channel.members:
                            if member.bot:
                                continue
                            
                            session_key = f"{member.id}_{voice_channel.id}"
                            
                            if session_key not in self._voice_sessions:
                                self._voice_sessions[session_key] = VoiceSession(
                                    user_id=member.id,
                                    guild_id=guild.id,
                                    channel_id=voice_channel.id,
                                    started_at=time.time(),
                                    xp_earned=0,
                                    coins_earned=0,
                                    messages_count=0
                                )
                            
                            session = self._voice_sessions[session_key]
                            session.xp_earned += 5
                            session.coins_earned += 2
                            
                            if session.xp_earned >= 30:
                                session.xp_earned += 10
                            if session.coins_earned >= 30:
                                session.coins_earned += 5
                            
                            await self._check_voice_roles(guild, member, session)
            except Exception as e:
                logger.error(f"Voice monitor error: {e}")
            
            await asyncio.sleep(60)

    async def _check_voice_roles(self, guild: discord.Guild, member: discord.Member, session: VoiceSession):
        settings = self.get_guild_settings(guild.id)
        voice_roles = settings.get("voice_roles", {})
        
        total_minutes = int((time.time() - session.started_at) / 60)
        
        for threshold, role_id in sorted(voice_roles.items()):
            if total_minutes >= int(threshold):
                role = guild.get_role(int(role_id))
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role)
                    except:
                        pass

    async def on_voice_join(self, member: discord.Member, channel: discord.VoiceChannel):
        if member.bot:
            return
        
        session_key = f"{member.id}_{channel.id}"
        
        self._voice_sessions[session_key] = VoiceSession(
            user_id=member.id,
            guild_id=channel.guild.id,
            channel_id=channel.id,
            started_at=time.time(),
            xp_earned=0,
            coins_earned=0,
            messages_count=0
        )

    async def on_voice_leave(self, member: discord.Member, channel: discord.VoiceChannel):
        if member.bot:
            return
        
        session_key = f"{member.id}_{channel.id}"
        
        if session_key in self._voice_sessions:
            session = self._voice_sessions[session_key]
            
            await self._award_voice_rewards(member.guild.id, member.id, session)
            
            del self._voice_sessions[session_key]

    async def _award_voice_rewards(self, guild_id: int, user_id: int, session: VoiceSession):
        if session.xp_earned == 0 and session.coins_earned == 0:
            return
        
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        user_data["xp"] = user_data.get("xp", 0) + session.xp_earned
        user_data["coins"] = user_data.get("coins", 0) + session.coins_earned
        minutes = int((time.time() - session.started_at) / 60)
        user_data["voice_minutes"] = user_data.get("voice_minutes", 0) + minutes

        # Track timestamped voice activity for analytics
        activity = dm.get_guild_data(guild_id, "voice_activity_log", [])
        activity.append({"timestamp": time.time(), "user_id": user_id, "minutes": minutes})
        # Keep only last 1000 entries
        dm.update_guild_data(guild_id, "voice_activity_log", activity[-1000:])
        
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)

    async def create_voice_event(self, guild_id: int, channel_id: int, event_type: str, settings: dict):
        event_id = f"voice_event_{guild_id}_{int(time.time())}"
        
        event = {
            "id": event_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "type": event_type,
            "settings": settings,
            "started_at": time.time(),
            "participants": [],
            "scores": {},
            "status": "active"
        }
        
        events = dm.get_guild_data(guild_id, "voice_events", {})
        events[event_id] = event
        dm.update_guild_data(guild_id, "voice_events", events)
        
        return event

    async def run_trivia_in_channel(self, channel: discord.VoiceChannel, topics: List[str]):
        event = await self.create_voice_event(
            channel.guild.id,
            channel.id,
            "trivia",
            {"topics": topics}
        )
        
        topic_str = ", ".join(topics)
        prompt = f"""Generate 10 trivia questions about {topic_str}.

Respond with JSON only:
{{
    "questions": [
        {{
            "question": "question text",
            "options": ["A", "B", "C", "D"],
            "correct": 0
        }}
    ]
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=channel.guild.id,
                user_id=0,
                user_input=prompt,
                system_prompt="You host voice trivia. Be fun and engaging."
            )
            
            event["questions"] = result.get("questions", [])
            
            embed = discord.Embed(
                title="🎤 Voice Trivia Started!",
                description=f"Topics: {topic_str}",
                color=discord.Color.gold()
            )
            embed.add_field(name="How to play", value="Answer questions in chat. First to answer correctly gets points!", inline=False)
            
            await channel.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Failed to start voice trivia: {e}")

    def get_voice_leaderboard(self, guild_id: int, timeframe: str = "weekly") -> List[dict]:
        cutoff = time.time()
        if timeframe == "daily":
            cutoff -= 86400
        elif timeframe == "weekly":
            cutoff -= 604800
        elif timeframe == "monthly":
            cutoff -= 2592000
        
        all_voice_data = {}
        
        data_dir = "data"
        import os
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_data = dm.load_json(filename[:-5])
                        for key, value in guild_data.items():
                            if key.startswith("user_"):
                                user_id = int(key[5:])
                                if user_id not in all_voice_data:
                                    all_voice_data[user_id] = 0
                                all_voice_data[user_id] += value.get("voice_minutes", 0)
                    except:
                        pass
        
        sorted_users = sorted(all_voice_data.items(), key=lambda x: x[1], reverse=True)[:10]
        
        leaderboard = []
        for i, (user_id, minutes) in enumerate(sorted_users):
            leaderboard.append({
                "rank": i + 1,
                "user_id": user_id,
                "voice_minutes": minutes,
                "xp": minutes * 5,
                "coins": minutes * 2
            })
        
        return leaderboard

    def get_hourly_voice_minutes(self, guild_id: int) -> int:
        """Get the total voice minutes for the guild in the last hour."""
        cutoff = time.time() - 3600
        total = 0

        activity = dm.get_guild_data(guild_id, "voice_activity_log", [])
        for entry in activity:
            if entry.get("timestamp", 0) > cutoff:
                total += entry.get("minutes", 0)

        # Also add currently active sessions
        for session_key, session in self._voice_sessions.items():
            if session.guild_id == guild_id:
                current_minutes = int((time.time() - session.started_at) / 60)
                total += current_minutes

        return total

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "voice_settings", settings)
        
        try:
            doc_channel = await guild.create_text_channel("voice-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        doc_embed = discord.Embed(title="🎤 Voice Activity System Guide", description="Earn rewards while in voice channels!", color=discord.Color.purple())
        doc_embed.add_field(name="📖 How It Works", value="Earn XP and coins simply by being in voice channels! Rewards scale with time spent.", inline=False)
        doc_embed.add_field(name="🎮 Available Commands", value="**!voiceleaderboard** - View voice activity leaderboard\n**!voicestats** - Check your voice stats\n**!help voice** - Show this guide", inline=False)
        doc_embed.add_field(name="💡 Rewards", value="• 5 XP per minute\n• 2 coins per minute\n• Bonus at 30 minutes!\n• Voice roles unlock at milestones", inline=False)
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** Just join a voice channel and you'll start earning!")
        
        help_embed = discord.Embed(title="🎤 Voice Activity System", description="Earn XP and coins while in voice channels.", color=discord.Color.green())
        help_embed.add_field(name="How it works", value="Earn 5 XP and 2 coins per minute in voice. Bonus rewards at 30 minutes. Voice roles unlock based on time spent.", inline=False)
        help_embed.add_field(name="!voiceleaderboard", value="View voice activity leaderboard.", inline=False)
        help_embed.add_field(name="!voicestats", value="Check your voice activity stats.", inline=False)
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["voiceleaderboard"] = json.dumps({
            "command_type": "voice_leaderboard"
        })
        custom_cmds["voicestats"] = json.dumps({
            "command_type": "voice_stats"
        })
        custom_cmds["help voice"] = json.dumps({
            "command_type": "help_embed",
            "title": "🎤 Voice Activity System",
            "description": "Earn XP and coins in voice channels.",
            "fields": [
                {"name": "!voiceleaderboard", "value": "View leaderboard.", "inline": False},
                {"name": "!voicestats", "value": "Check your stats.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
