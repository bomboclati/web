import discord
from discord.ext import commands
import asyncio
import json
import time
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from data_manager import dm
from logger import logger

class WelcomeLeaveSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "welcome_config", {
            "welcome_enabled": True,
            "leave_enabled": True,
            "welcome_channel": None,
            "leave_channel": None,
            "welcome_message": "Welcome {user.mention} to **{server}**! You are member #{server.membercount}.",
            "leave_message": "**{user.name}** has left the server.",
            "welcome_color": "#5865F2",
            "welcome_image": None,
            "show_member_number": True,
            "show_account_age": True,
            "ping_on_welcome": False,
            "rules_summary": None,
            "important_channels": [], # List of channel IDs
            "stats": {"joins_today": 0, "joins_week": 0, "leaves_today": 0}
        })

    def _format_message(self, text: str, member: discord.Member, guild: discord.Guild) -> str:
        if not text: return ""
        
        now = datetime.now(timezone.utc)
        replacements = {
            "{user}": member.mention,
            "{user.mention}": member.mention,
            "{user.name}": member.name,
            "{user.id}": str(member.id),
            "{server}": guild.name,
            "{server.membercount}": str(guild.member_count),
            "{date}": now.strftime("%Y-%m-%d"),
            "{time}": now.strftime("%H:%M:%S")
        }
        
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        return text

    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        config = self.get_config(guild.id)
        if not config.get("welcome_enabled", True): return

        ch_id = config.get("welcome_channel")
        channel = guild.get_channel(ch_id) if ch_id else None
        if not channel: return

        # Variables
        desc = self._format_message(config.get("welcome_message"), member, guild)
        
        # Extra info
        if config.get("show_account_age"):
            age = (datetime.now(timezone.utc) - member.created_at).days
            desc += f"\n\n🗓️ **Account Age:** Created {age} days ago."
        
        if config.get("show_member_number"):
            n = guild.member_count
            suffix = ['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)] if not (11 <= (n % 100) <= 13) else 'th'
            desc += f"\n👤 You are our **{n}{suffix}** member!"

        if config.get("rules_summary"):
            desc += f"\n\n📜 **Rules Summary:**\n{config.get('rules_summary')}"

        if config.get("important_channels"):
            links = []
            for cid in config.get("important_channels"):
                ch = guild.get_channel(cid)
                if ch: links.append(ch.mention)
            if links:
                desc += f"\n\n🔗 **Important Channels:** {' '.join(links)}"

        color_hex = config.get("welcome_color", "#5865F2").replace("#", "")
        try:
            color = discord.Color(int(color_hex, 16))
        except:
            color = discord.Color.blue()

        embed = discord.Embed(
            title=f"Welcome to {guild.name}!",
            description=desc,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if config.get("welcome_image"):
            embed.set_image(url=config.get("welcome_image"))

        content = member.mention if config.get("ping_on_welcome") else None
        await channel.send(content=content, embed=embed)

        # Update stats
        config["stats"]["joins_today"] = config["stats"].get("joins_today", 0) + 1
        dm.update_guild_data(guild.id, "welcome_config", config)

    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        config = self.get_config(guild.id)
        if not config.get("leave_enabled", True): return

        ch_id = config.get("leave_channel")
        channel = guild.get_channel(ch_id) if ch_id else None
        if not channel: return

        desc = self._format_message(config.get("leave_message"), member, guild)

        # Stay duration
        if member.joined_at:
            stay = (datetime.now(timezone.utc) - member.joined_at).days
            desc += f"\n⏱️ They were with us for **{stay}** days."

        # Roles
        roles = [r.mention for r in member.roles if r != guild.default_role]
        if roles:
            desc += f"\n🎭 **Roles:** {', '.join(roles)}"

        # Kick/Ban check (Best effort from Audit Log)
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
                if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    desc += f"\n👢 **Kicked by staff.** Reason: {entry.reason or 'No reason provided.'}"
                    break
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    desc += f"\n🔨 **Banned from server.** Reason: {entry.reason or 'No reason provided.'}"
                    break
        except: pass

        embed = discord.Embed(
            title="Goodbye!",
            description=desc,
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        await channel.send(embed=embed)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        config = self.get_config(guild.id)
        config["welcome_enabled"] = True
        
        welcome = discord.utils.get(guild.text_channels, name="welcome") or await guild.create_text_channel("welcome")
        config["welcome_channel"] = welcome.id
        dm.update_guild_data(guild.id, "welcome_config", config)
        
        await interaction.followup.send(f"✅ Welcome system enabled in {welcome.mention}!")
        return True
