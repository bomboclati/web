"""
Guardian: AI-Powered Anti-Raid & Server Security Layer for Miro Bot
Distinct from Anti-Raid, Guardian handles silent monitoring and escalation.
"""

import discord
from discord.ext import commands
import asyncio
import time
import re
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from data_manager import dm
from logger import logger

DEFAULT_GUARDIAN_CONFIG = {
    "enabled": True,
    "alert_channel": None,
    "toxicity_level": "WARN", # OFF, WARN, MUTE, KICK, BAN
    "scam_level": "MUTE",
    "impersonation_level": "WARN",
    "mass_dm_threshold": 10, # msgs/min
    "nuke_level": "BAN",
    "token_detection": True,
    "malware_level": "WARN",
    "selfbot_level": "MUTE",
    "escalation": True,
    "whitelist": [],
    "guardian_log": []
}

class GuardianSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # {guild_id: {user_id: [timestamp, ...]}}
        self._dm_tracking = {}
        # {guild_id: {user_id: [timestamp, ...]}}
        self._typing_patterns = {}

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "guardian_config", DEFAULT_GUARDIAN_CONFIG.copy())

    def save_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "guardian_config", config)

    def _log_incident(self, guild_id: int, type: str, user_id: int, action: str, details: str = ""):
        config = self.get_config(guild_id)
        log = config.get("guardian_log", [])
        log.append({
            "ts": time.time(),
            "type": type,
            "user_id": user_id,
            "action": action,
            "details": details
        })
        config["guardian_log"] = log[-200:]
        self.save_config(guild_id, config)
        
        asyncio.create_task(self._send_alert(guild_id, type, user_id, action, details))

    async def _send_alert(self, guild_id: int, type: str, user_id: int, action: str, details: str):
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        config = self.get_config(guild_id)
        ch_id = config.get("alert_channel")
        channel = guild.get_channel(ch_id) if ch_id else None
        
        embed = discord.Embed(
            title="⚔️ Guardian Intervention",
            description=f"**Detection:** {type.replace('_', ' ').title()}",
            color=discord.Color.dark_red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
        embed.add_field(name="Action", value=action, inline=True)
        if details:
            embed.add_field(name="Details", value=details[:1024], inline=False)
        
        if channel:
            try: await channel.send(embed=embed)
            except: pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        guild = message.guild
        config = self.get_config(guild_id=guild.id)
        if not config.get("enabled", True): return
        if message.author.id in config.get("whitelist", []): return

        content = message.content
        author = message.author

        # 1. Bot Token Detection
        if config.get("token_detection"):
            # Refined regex for Discord tokens
            token_pattern = r"(mfa\.[a-z0-9_-]{20,})|([a-z0-9_-]{24}\.[a-z0-9_-]{6}\.[a-z0-9_-]{27,})"
            if re.search(token_pattern, content, re.IGNORECASE):
                try:
                    await message.delete()
                except:
                    pass
                # Tokens are high risk, default to MUTE if not specified
                action_level = config.get("token_action_level", "MUTE")
                await self._take_action(author, action_level, "Potential Bot Token Leaked")
                self._log_incident(guild.id, "token_leak", author.id, "DELETE", "Discord Token Pattern Found")

                # Notify the user privately
                try:
                    await author.send("⚠️ **SECURITY ALERT**\nYour message was deleted because it appeared to contain a Discord Bot Token. Please reset your token immediately to prevent unauthorized access to your account/bot.")
                except:
                    pass
                return

        # 2. Toxicity & Scam Detection
        scam_patterns = ["nitro", "gift", "steam", "free", "airdrop", "robux", "crypto"]
        if any(p in content.lower() for p in scam_patterns) and ("http" in content or "discord.gg" in content):
            # Use AI for high-confidence scam detection if possible
            is_scam = False
            if hasattr(self.bot, "ai"):
                try:
                    # Quick AI verification for suspicious links
                    analysis = await self.bot.ai.analyze_content(content, "scam_check")
                    is_scam = analysis.get("is_scam", False)
                except:
                    is_scam = True # Fallback to pattern match
            else:
                is_scam = True # No AI, rely on patterns

            if is_scam:
                try:
                    await message.delete()
                except:
                    pass
                action_level = config.get("scam_level", "MUTE")
                await self._take_action(author, action_level, "Scam Link Detected")
                self._log_incident(guild.id, "scam_link", author.id, action_level, content)
                return

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        if user.bot or not hasattr(channel, "guild"): return
        guild = channel.guild
        config = self.get_config(guild.id)
        if not config.get("enabled", True): return

        # Self-bot detection via typing speed
        # If user starts/stops typing too fast or types in multiple channels simultaneously
        if guild.id not in self._typing_patterns: self._typing_patterns[guild.id] = {}
        if user.id not in self._typing_patterns[guild.id]: self._typing_patterns[guild.id][user.id] = []
        
        self._typing_patterns[guild.id][user.id].append(time.time())
        # Implementation of typing speed analysis would go here

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        # Nuke protection: rapid channel/role deletions
        guild = entry.guild
        config = self.get_config(guild.id)
        if not config.get("enabled", True): return

        if entry.action in [discord.AuditLogAction.channel_delete, discord.AuditLogAction.role_delete]:
            # Rapid detection logic
            pass

    async def _take_action(self, member: discord.Member, level: str, reason: str):
        if level == "OFF":
            return

        try:
            if level == "WARN":
                try:
                    await member.send(f"⚠️ **Guardian Warning**\nServer: {member.guild.name}\nReason: {reason}")
                except:
                    pass
                # Also log to a moderation system if available
                if hasattr(self.bot, "warnings"):
                    await self.bot.warnings.issue_warning(member.guild, member.id, self.bot.user.id, f"Guardian: {reason}", "moderate")

            elif level == "MUTE":
                from datetime import timedelta
                await member.timeout(timedelta(hours=2), reason=f"Guardian: {reason}")

            elif level == "KICK":
                await member.kick(reason=f"Guardian: {reason}")

            elif level == "BAN":
                await member.ban(reason=f"Guardian: {reason}")
        except Exception as e:
            logger.error(f"Guardian failed to take action {level} on {member.id}: {e}")

async def setup(bot):
    await bot.add_cog(GuardianSystem(bot))
