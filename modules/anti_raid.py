import discord
from discord.ext import commands
import asyncio
import json
import time
import re
from typing import Dict, List, Optional
from dataclasses import dataclass

from data_manager import dm
from logger import logger


@dataclass
class RaidAlert:
    guild_id: int
    detected_at: float
    join_count: int
    verified: bool
    action_taken: str


class AntiRaidSystem:
    def __init__(self, bot):
        self.bot = bot
        self._join_history: Dict[int, List[float]] = {}
        self._raid_alerts: Dict[int, RaidAlert] = {}
        self._suspicious_members: Dict[int, dict] = {}
        self._load_settings()
        self._start_monitoring()

    def _load_settings(self):
        data = dm.load_json("anti_raid_settings", default={})
        self._suspicious_members = data.get("suspicious", {})

    def _save_settings(self):
        data = {"suspicious": self._suspicious_members}
        dm.save_json("anti_raid_settings", data)

    def _start_monitoring(self):
        asyncio.create_task(self._raid_monitor_loop())

    async def _raid_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    settings = self.get_guild_settings(guild.id)
                    
                    if not settings.get("enabled", True):
                        continue
                    
                    await self._check_raid_patterns(guild, settings)
                    await self._check_suspicious_names(guild, settings)
            except Exception as e:
                logger.error(f"Raid monitor error: {e}")
            
            await asyncio.sleep(60)

    async def _check_raid_patterns(self, guild: discord.Guild, settings: dict):
        if guild.id not in self._join_history:
            self._join_history[guild.id] = []
        
        current_time = time.time()
        window_seconds = settings.get("join_window_seconds", 60)
        threshold = settings.get("join_threshold", 5)
        
        self._join_history[guild.id] = [
            t for t in self._join_history[guild.id]
            if current_time - t < window_seconds
        ]
        
        if len(self._join_history[guild.id]) >= threshold:
            if guild.id in self._raid_alerts:
                existing = self._raid_alerts[guild.id]
                if current_time - existing.detected_at < 300:
                    return
            
            alert = RaidAlert(
                guild_id=guild.id,
                detected_at=current_time,
                join_count=len(self._join_history[guild.id]),
                verified=False,
                action_taken=""
            )
            self._raid_alerts[guild.id] = alert
            
            await self._trigger_raid_protection(guild, settings, alert)

    async def _trigger_raid_protection(self, guild: discord.Guild, settings: dict, alert: RaidAlert):
        actions = settings.get("raid_actions", ["lockdown"])
        
        if "lockdown" in actions:
            await self._lockdown_server(guild, settings)
        
        if "verification" in actions:
            verify_role_id = settings.get("verification_role")
            if verify_role_id:
                for member in guild.members:
                    if not member.bot:
                        role = guild.get_role(int(verify_role_id))
                        if role:
                            try:
                                await member.add_roles(role)
                            except:
                                pass
        
        if "announce" in actions:
            log_channel_id = settings.get("log_channel")
            if log_channel_id:
                channel = guild.get_channel(int(log_channel_id))
                if channel:
                    embed = discord.Embed(
                        title="🚨 RAID ALERT",
                        description=f"Detected {alert.join_count} joins in under a minute. Protection activated.",
                        color=discord.Color.red()
                    )
                    embed.add_field(name="Actions Taken", value=", ".join(actions), inline=False)
                    
                    await channel.send(embed=embed)
        
        alert.action_taken = ", ".join(actions)
        alert.verified = True
        
        asyncio.create_task(self._auto_unlock(guild, settings))

    async def _lockdown_server(self, guild: discord.Guild, settings: dict):
        lockdown_duration = settings.get("lockdown_duration_minutes", 15)
        
        for channel in guild.channels:
            try:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(
                        guild.default_role,
                        send_messages=False,
                        add_reactions=False
                    )
            except:
                pass
        
        log_channel_id = settings.get("log_channel")
        if log_channel_id:
            channel = guild.get_channel(int(log_channel_id))
            if channel:
                await channel.send(f"🔒 Server locked down for {lockdown_duration} minutes due to raid detection.")

    async def _auto_unlock(self, guild: discord.Guild, settings: dict):
        lockdown_duration = settings.get("lockdown_duration_minutes", 15)
        
        await asyncio.sleep(lockdown_duration * 60)
        
        for channel in guild.channels:
            try:
                if isinstance(channel, discord.TextChannel):
                    await channel.set_permissions(
                        guild.default_role,
                        send_messages=None,
                        add_reactions=None
                    )
            except:
                pass
        
        log_channel_id = settings.get("log_channel")
        if log_channel_id:
            channel = guild.get_channel(int(log_channel_id))
            if channel:
                await channel.send("🔓 Server lockdown lifted.")

    async def _check_suspicious_names(self, guild: discord.Guild, settings: dict):
        if not settings.get("suspicious_name_detection", True):
            return
        
        suspicious_patterns = settings.get("suspicious_patterns", [
            r"^[\d\-]{5,}$",
            r"(.)\1{4,}",
            r"^[a-z]{1,3}$",
            r"(discord|free|nitro|gift|giveaway)",
        ])
        
        for member in guild.members:
            if member.bot:
                continue
            
            member_key = f"{guild.id}_{member.id}"
            
            if member_key in self._suspicious_members:
                continue
            
            name_lower = member.display_name.lower()
            
            is_suspicious = False
            
            for pattern in suspicious_patterns:
                if re.search(pattern, member.display_name, re.IGNORECASE):
                    is_suspicious = True
                    break
            
            if is_suspicious:
                self._suspicious_members[member_key] = {
                    "reason": "suspicious_name",
                    "detected_at": time.time(),
                    "name": member.display_name
                }
                self._save_settings()
                
                verify_role = settings.get("verification_role")
                if verify_role:
                    role = guild.get_role(int(verify_role))
                    if role:
                        try:
                            await member.add_roles(role)
                        except:
                            pass

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "anti_raid_settings", {
            "enabled": True,
            "join_threshold": 5,
            "join_window_seconds": 60,
            "raid_actions": ["lockdown", "verification", "announce"],
            "lockdown_duration_minutes": 15,
            "verification_role": None,
            "log_channel": None,
            "suspicious_name_detection": True,
            "suspicious_patterns": [
                r"^[\d\-]{5,}$",
                r"(.)\1{4,}",
                r"^[a-z]{1,3}$"
            ],
            "spam_filter": True,
            "spam_message_threshold": 5,
            "spam_time_window": 10
        })

    async def check_spam(self, message: discord.Message):
        if message.author.bot:
            return False
        
        settings = self.get_guild_settings(message.guild.id)
        
        if not settings.get("spam_filter", True):
            return False
        
        spam_key = f"{message.guild.id}_{message.author.id}"
        
        if not hasattr(self, "_spam_messages"):
            self._spam_messages = {}
        
        if spam_key not in self._spam_messages:
            self._spam_messages[spam_key] = []
        
        current_time = time.time()
        window = settings.get("spam_time_window", 10)
        threshold = settings.get("spam_message_threshold", 5)
        
        self._spam_messages[spam_key] = [
            t for t in self._spam_messages[spam_key]
            if current_time - t < window
        ]
        
        self._spam_messages[spam_key].append(current_time)
        
        if len(self._spam_messages[spam_key]) >= threshold:
            try:
                await message.author.timeout(discord.utils.utcnow() + timedelta(minutes=5), reason="Spam detected")
                
                log_channel_id = settings.get("log_channel")
                if log_channel_id:
                    channel = message.guild.get_channel(int(log_channel_id))
                    if channel:
                        await channel.send(f"🔇 {message.author.mention} was muted for spam.")
                
                return True
            except:
                pass
        
        return False

    def get_raid_status(self, guild_id: int) -> Optional[RaidAlert]:
        return self._raid_alerts.get(guild_id)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "anti_raid_settings", settings)
        
        try:
            doc_channel = await guild.create_text_channel("security-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        doc_embed = discord.Embed(title="🛡️ Anti-Raid Protection Guide", description="Keep your server safe!", color=discord.Color.red())
        doc_embed.add_field(name="📖 How It Works", value="Automatically detects suspicious activity like mass joins and spam. Takes protective actions to keep the server safe.", inline=False)
        doc_embed.add_field(name="Features", value="• Mass join detection\n• Server lockdown\n• Verification roles\n• Suspicious name detection\n• Spam filtering", inline=False)
        doc_embed.add_field(name="Commands", value="**!raidstatus** - Check protection status\n**!help antiraid** - Show this guide", inline=False)
        
        await doc_channel.send(embed=doc_embed)
        
        help_embed = discord.Embed(title="🛡️ Anti-Raid Protection", description="Automatic protection against raids and spam attacks.", color=discord.Color.green())
        help_embed.add_field(name="How it works", value="Monitors join patterns, detects mass joins, locks down server, applies verification roles. Also filters suspicious usernames and spam.", inline=False)
        help_embed.add_field(name="!raidstatus", value="Check current raid protection status.", inline=False)
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["raidstatus"] = json.dumps({
            "command_type": "raid_status"
        })
        custom_cmds["help antiraid"] = json.dumps({
            "command_type": "help_embed",
            "title": "🛡️ Anti-Raid Protection",
            "description": "Automatic raid protection.",
            "fields": [
                {"name": "!raidstatus", "value": "Check raid status.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from datetime import timedelta
from discord import app_commands
