import discord
from discord.ext import commands
import asyncio
import json
import time
import re
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger

class AntiRaidSystem:
    def __init__(self, bot):
        self.bot = bot
        self._join_history: Dict[int, List[float]] = {}
        # Track mentions: {guild_id: {user_id: [timestamp, ...]}}
        self._mention_history: Dict[int, Dict[int, List[float]]] = {}
        # Track messages for duplicate detection: {guild_id: {user_id: [content, ...]}}
        self._msg_content_history: Dict[int, Dict[int, List[str]]] = {}

    def get_guild_settings(self, guild_id: int) -> dict:
        """Retrieve guild anti-raid settings, unified with config panel key."""
        return dm.get_guild_data(guild_id, "antiraid_config", {
            "enabled": True,
            "mass_join_threshold": 10,
            "mass_join_window": 10,
            "auto_lockdown": True,
            "action": "lockdown", # lockdown, kick, ban, mute
            "min_account_age_days": 0,
            "age_filter_enabled": False,
            "avatar_filter_enabled": False,
            "quarantine_role_id": None,
            "alert_channel_id": None,
            "raid_log": [],
            "link_spam_enabled": True,
            "invite_filter_enabled": True,
            "mention_threshold": 5,
            "mention_filter_enabled": True,
            "duplicate_threshold": 3,
            "duplicate_filter_enabled": True,
            "emoji_threshold": 15,
            "emoji_filter_enabled": True
        })

    def save_settings(self, guild_id: int, settings: dict):
        dm.update_guild_data(guild_id, "antiraid_config", settings)

    def _log_incident(self, guild_id: int, type: str, members: List[int], action: str):
        settings = self.get_guild_settings(guild_id)
        log = settings.get("raid_log", [])
        log.append({
            "ts": time.time(),
            "type": type,
            "members": members,
            "action": action
        })
        settings["raid_log"] = log[-100:]
        self.save_settings(guild_id, settings)
        
        # Send alert
        asyncio.create_task(self._send_alert(guild_id, type, members, action))

    async def _send_alert(self, guild_id: int, type: str, members: List[int], action: str):
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        settings = self.get_guild_settings(guild_id)
        ch_id = settings.get("alert_channel_id")
        channel = guild.get_channel(ch_id) if ch_id else None
        
        embed = discord.Embed(
            title="🛡️ Anti-Raid Alert",
            description=f"**Trigger:** {type.replace('_', ' ').title()}",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Action Taken", value=action.upper(), inline=True)
        embed.add_field(name="Impacted Members", value=f"{len(members)} users" if len(members) > 5 else ", ".join([f"<@{m}>" for m in members]), inline=False)
        
        if channel:
            try: await channel.send(embed=embed)
            except: pass
            
        # Notify owner on major raids
        if type == "mass_join" and guild.owner:
            try: await guild.owner.send(f"⚠️ **Raid Alert in {guild.name}**\nMass join detected! Action taken: {action.upper()}")
            except: pass

    async def handle_join(self, member: discord.Member):
        guild = member.guild
        settings = self.get_guild_settings(guild.id)
        if not settings.get("enabled", True): return

        # 1. New Account Check
        if settings.get("age_filter_enabled"):
            min_age = settings.get("min_account_age_days", 0)
            if (discord.utils.utcnow() - member.created_at).days < min_age:
                await self._take_action(member, settings.get("action"), "Account too young")
                self._log_incident(guild.id, "new_account_join", [member.id], settings.get("action"))
                return

        # 2. Mass Join Detection
        if guild.id not in self._join_history: self._join_history[guild.id] = []
        now = time.time()
        window = settings.get("mass_join_window", 10)
        threshold = settings.get("mass_join_threshold", 10)
        
        self._join_history[guild.id] = [t for t in self._join_history[guild.id] if now - t < window]
        self._join_history[guild.id].append(now)
        
        if len(self._join_history[guild.id]) >= threshold:
            if settings.get("auto_lockdown"):
                await self._lockdown(guild)
                self._log_incident(guild.id, "mass_join", [], "lockdown")

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        guild = message.guild
        settings = self.get_guild_settings(guild.id)
        if not settings.get("enabled", True): return

        content = message.content
        author = message.author

        # 1. Mention Spam
        if settings.get("mention_filter_enabled"):
            mentions = len(message.mentions) + len(message.role_mentions)
            if mentions >= settings.get("mention_threshold", 5) or message.mention_everyone:
                await message.delete()
                await self._take_action(author, "mute", "Mention spam")
                self._log_incident(guild.id, "mention_spam", [author.id], "mute")
                return

        # 2. Duplicate Spam
        if settings.get("duplicate_filter_enabled"):
            if guild.id not in self._msg_content_history: self._msg_content_history[guild.id] = {}
            if author.id not in self._msg_content_history[guild.id]: self._msg_content_history[guild.id][author.id] = []
            
            history = self._msg_content_history[guild.id][author.id]
            history.append(content)
            if len(history) > 10: history.pop(0)
            
            if len(history) >= 3 and all(m == content for m in history[-3:]):
                await message.delete()
                await self._take_action(author, "mute", "Duplicate message spam")
                self._log_incident(guild.id, "duplicate_spam", [author.id], "mute")
                return

        # 3. Link/Invite Spam
        if settings.get("link_spam_enabled") and re.search(r"https?://", content):
            # Check for discord invites
            if settings.get("invite_filter_enabled") and ("discord.gg/" in content or "discord.com/invite/" in content):
                await message.delete()
                await self._take_action(author, "warn", "Invite link spam")
                return
            
        # 4. Emoji Spam
        if settings.get("emoji_filter_enabled"):
            emojis = len(re.findall(r"<a?:\w+:\d+>|[\u263a-\U0001f645]", content))
            if emojis > settings.get("emoji_threshold", 15):
                await message.delete()
                await self._take_action(author, "warn", "Emoji spam")
                return

    async def _take_action(self, member: discord.Member, action: str, reason: str):
        try:
            if action == "kick": await member.kick(reason=reason)
            elif action == "ban": await member.ban(reason=reason)
            elif action == "mute":
                await member.timeout(timedelta(hours=1), reason=reason)
            elif action == "lockdown":
                await self._lockdown(member.guild)
        except: pass

    async def _lockdown(self, guild: discord.Guild):
        for ch in guild.text_channels:
            try:
                await ch.set_permissions(guild.default_role, send_messages=False, reason="Anti-Raid Auto-Lockdown")
            except: pass

    async def lift_lockdown(self, guild: discord.Guild):
        for ch in guild.text_channels:
            try:
                await ch.set_permissions(guild.default_role, send_messages=None, reason="Anti-Raid Lockdown Lifted")
            except: pass

    def start_monitoring(self):
        # We use bot event listeners instead of a loop for real-time response
        pass

async def setup(bot):
    bot.anti_raid = AntiRaidSystem(bot)
