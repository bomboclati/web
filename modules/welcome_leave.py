import discord
from discord.ext import commands
import asyncio
import json
import time
import io
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from data_manager import dm
from logger import logger


class WelcomeDMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Verify Now", style=discord.ButtonStyle.success, custom_id="wdm_verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "verify")

    @discord.ui.button(label="📜 Read Rules", style=discord.ButtonStyle.primary, custom_id="wdm_rules")
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "rules")

    @discord.ui.button(label="🎭 Pick Roles", style=discord.ButtonStyle.primary, custom_id="wdm_roles")
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "roles")

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.primary, custom_id="wdm_ticket")
    async def ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "ticket")

    @discord.ui.button(label="📋 Apply for Staff", style=discord.ButtonStyle.primary, custom_id="wdm_apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "apply")

    @discord.ui.button(label="🆘 Get Help", style=discord.ButtonStyle.danger, custom_id="wdm_help")
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "help")

    @discord.ui.button(label="📊 Server Info", style=discord.ButtonStyle.secondary, custom_id="wdm_info")
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "info")

    @discord.ui.button(label="🔕 Opt Out", style=discord.ButtonStyle.secondary, custom_id="wdm_optout")
    async def optout(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_button(interaction, "optout")

    async def _handle_button(self, interaction: discord.Interaction, action: str):
        # In a real DM interaction, interaction.guild will be None.
        # We need to find the shared guild.
        user = interaction.user
        shared_guilds = [g for g in interaction.client.guilds if g.get_member(user.id)]
        if not shared_guilds:
            return await interaction.response.send_message("❌ No shared servers found.", ephemeral=True)

        guild = shared_guilds[0] # Use first one for simplicity

        if action == "optout":
            opted_out = dm.get_guild_data(guild.id, "welcomedm_optout", [])
            if user.id not in opted_out:
                opted_out.append(user.id)
                dm.update_guild_data(guild.id, "welcomedm_optout", opted_out)
            return await interaction.response.send_message("✅ You have opted out of Welcome DMs from this bot.", ephemeral=True)

        if action == "verify":
            ch_id = dm.get_guild_data(guild.id, "verify_channel")
            if ch_id: return await interaction.response.send_message(f"Go to <#{ch_id}> to verify!", ephemeral=True)
            return await interaction.response.send_message("Verification channel not found.", ephemeral=True)

        if action == "rules":
            ch_id = dm.get_guild_data(guild.id, "rules_channel")
            if ch_id: return await interaction.response.send_message(f"Read our rules here: <#{ch_id}>", ephemeral=True)
            return await interaction.response.send_message("Rules channel not found.", ephemeral=True)

        if action == "ticket":
            # Direct ticket creation from DM
            from modules.tickets import TicketModal
            await interaction.response.send_modal(TicketModal())
            return

        if action == "apply":
            from modules.applications import ApplicationPersistentView
            view = ApplicationPersistentView()
            await view.apply_now.callback(interaction, view.apply_now)
            return

        if action == "roles":
            await interaction.response.send_message("Please visit the #roles channel in the server to pick your roles!", ephemeral=True)
            return

        if action == "help":
            await interaction.response.send_message("Use `!help` in the server to see all available commands!", ephemeral=True)
            return

        if action == "info":
            await interaction.response.send_message(f"Welcome to **{guild.name}**! We use Miro AI for automation and engagement.", ephemeral=True)
            return

        await interaction.response.send_message(f"Action '{action}' processed.", ephemeral=True)

class WelcomeLeaveSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_welcome_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "welcome_config", {
            "enabled": False,
            "channel_id": None,
            "message": "Welcome {user} to {server}! 🎉",
            "embed_title": "Welcome to {server}!",
            "embed_color": 0x2ecc71,
            "thumbnail_enabled": True,
            "image_url": None,
            "show_member_number": True,
            "show_account_age": True,
            "ping_on_welcome": False,
            "rules_summary": None,
            "important_channels": []
        })

    def get_leave_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "leave_config", {
            "enabled": False,
            "channel_id": None,
            "message": "{user} has left {server}. Goodbye! 👋",
            "embed_title": "Member Left",
            "embed_color": 0xe74c3c,
            "show_duration": True,
            "show_roles": True,
            "show_reason": True
        })

    def get_dm_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "welcomedm_config", {
            "enabled": False,
            "message": "Welcome to {server}! We're glad to have you here.",
            "embed_color": 0x3498db,
            "enabled_buttons": ["verify", "rules", "roles", "ticket", "apply", "help", "info", "optout"],
            "server_info_content": "Welcome to our community! Please check out our channels.",
            "help_role_id": None,
            "apply_redirect_id": None
        })

    def _format_message(self, member: discord.Member, text: str) -> str:
        if not text:
            return ""

        guild = member.guild
        now = datetime.now(timezone.utc)

        # Account age in days
        account_age = (now - member.created_at).days

        # Ordinal for member number
        def get_ordinal(n):
            if 11 <= (n % 100) <= 13:
                suffix = 'th'
            else:
                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
            return f"{n:,}{suffix}"

        # Ensure member count is as accurate as possible
        m_count = guild.member_count

        replacements = {
            "{user}": member.mention,
            "{user.mention}": member.mention,
            "{user.name}": member.name,
            "{user.id}": str(member.id),
            "{server}": guild.name,
            "{server.membercount}": f"{m_count:,}",
            "{date}": now.strftime("%Y-%m-%d"),
            "{time}": now.strftime("%H:%M:%S UTC"),
            "{member_number}": get_ordinal(m_count),
            "{account_age}": f"{account_age} days",
            "{join_position}": f"member #{m_count:,}"
        }

        for placeholder, value in replacements.items():
            text = text.replace(placeholder, str(value))

        return text

    def _track_stat(self, guild_id: int, stat_type: str):
        """Track join/leave stats."""
        history_key = f"wl_history_{stat_type}"
        history = dm.get_guild_data(guild_id, history_key, [])
        now = time.time()
        history.append(now)

        # Keep only last 31 days
        cutoff = now - (31 * 24 * 60 * 60)
        history = [ts for ts in history if ts > cutoff]
        dm.update_guild_data(guild_id, history_key, history)

    def get_stats(self, guild_id: int) -> dict:
        now = time.time()
        joins = dm.get_guild_data(guild_id, "wl_history_joins", [])
        leaves = dm.get_guild_data(guild_id, "wl_history_leaves", [])

        def count_since(history, seconds):
            cutoff = now - seconds
            return sum(1 for ts in history if ts > cutoff)

        return {
            "joins_today": count_since(joins, 24*3600),
            "joins_week": count_since(joins, 7*24*3600),
            "joins_month": count_since(joins, 30*24*3600),
            "leaves_today": count_since(leaves, 24*3600),
            "leaves_week": count_since(leaves, 7*24*3600)
        }

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        
        guild_id = member.guild.id
        self._track_stat(guild_id, "joins")
        
        # 1. Channel Welcome
        config = self.get_welcome_config(guild_id)
        if config.get("enabled") and config.get("channel_id"):
            channel = member.guild.get_channel(int(config["channel_id"]))
            if channel:
                content = member.mention if config.get("ping_on_welcome") else None
                
                embed = discord.Embed(
                    title=self._format_message(member, config.get("embed_title", "Welcome!")),
                    description=self._format_message(member, config.get("message", "")),
                    color=config.get("embed_color", 0x2ecc71)
                )
                
                if config.get("thumbnail_enabled"):
                    embed.set_thumbnail(url=member.display_avatar.url)
                
                if config.get("image_url"):
                    embed.set_image(url=config["image_url"])

                if config.get("show_member_number"):
                    embed.add_field(name="Member Number", value=f"You are our {self._get_member_number(member.guild)} member!", inline=True)

                if config.get("show_account_age"):
                    now = datetime.now(timezone.utc)
                    age = (now - member.created_at).days
                    embed.add_field(name="Account Age", value=f"Created {age} days ago", inline=True)

                if config.get("rules_summary"):
                    embed.add_field(name="📜 Server Rules", value=config["rules_summary"], inline=False)

                if config.get("important_channels"):
                    channels_str = " ".join([f"<#{cid}>" for cid in config["important_channels"]])
                    if channels_str:
                        embed.add_field(name="📍 Key Channels", value=channels_str, inline=False)

                await channel.send(content=content, embed=embed)

        # 2. Welcome DM
        dm_config = self.get_dm_config(guild_id)
        if dm_config.get("enabled"):
            # Check if user opted out
            opted_out = dm.get_guild_data(guild_id, "welcomedm_optout", [])
            if member.id not in opted_out:
                try:
                    embed = discord.Embed(
                        title=f"Welcome to {member.guild.name}!",
                        description=self._format_message(member, dm_config.get("message", "")),
                        color=dm_config.get("embed_color", 0x3498db)
                    )
                    embed.set_thumbnail(url=member.guild.icon.url if member.guild.icon else None)
                    embed.add_field(name="Server Stats", value=f"👥 {member.guild.member_count:,} members", inline=True)

                    view = WelcomeDMView()
                    # We can't easily filter persistent view buttons per instance without making them non-persistent
                    # or adding logic in the button callback to check config.
                    # Given the persistence requirement, let's keep all buttons and handle them.

                    await member.send(embed=embed, view=view)

                    # Track DM stats
                    stats = dm.get_guild_data(guild_id, "welcomedm_stats", {"sent": 0, "optout": 0, "verify_clicks": 0})
                    stats["sent"] += 1
                    dm.update_guild_data(guild_id, "welcomedm_stats", stats)
                except discord.Forbidden:
                    pass

    def _get_member_number(self, guild):
        def get_ordinal(n):
            if 11 <= (n % 100) <= 13:
                suffix = 'th'
            else:
                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
            return f"{n:,}{suffix}"
        return get_ordinal(guild.member_count)

    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        
        guild_id = member.guild.id
        self._track_stat(guild_id, "leaves")
        
        config = self.get_leave_config(guild_id)
        if config.get("enabled") and config.get("channel_id"):
            channel = member.guild.get_channel(int(config["channel_id"]))
            if channel:
                embed = discord.Embed(
                    title=self._format_message(member, config.get("embed_title", "Goodbye")),
                    description=self._format_message(member, config.get("message", "")),
                    color=config.get("embed_color", 0xe74c3c)
                )
                
                if config.get("show_duration"):
                    joined_at = member.joined_at
                    if joined_at:
                        duration = datetime.now(timezone.utc) - joined_at
                        days = duration.days
                        hours = (duration.seconds // 3600)
                        minutes = (duration.seconds // 60) % 60
                        embed.add_field(name="Stay Duration", value=f"{days}d {hours}h {minutes}m", inline=True)

                if config.get("show_roles"):
                    roles = [role.mention for role in member.roles if role.name != "@everyone"]
                    if roles:
                        embed.add_field(name="Roles", value=" ".join(roles[:10]), inline=False)

                if config.get("show_reason"):
                    # Attempt to fetch last kick/ban from audit log
                    try:
                        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                            if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 60:
                                embed.add_field(name="Kick Reason", value=entry.reason or "No reason provided", inline=False)
                                break
                        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                            if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 60:
                                embed.add_field(name="Ban Reason", value=entry.reason or "No reason provided", inline=False)
                                break
                    except:
                        pass

                await channel.send(embed=embed)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        # Implementation for /autosetup integration
        return True

from discord import app_commands
