import discord
from discord.ext import commands
from discord import ui
import asyncio
import time
import json
from typing import Dict, List, Optional, Any

from data_manager import dm
from logger import logger

class WelcomeDMView(ui.View):
    """The interactive view sent to new members in DM."""
    def __init__(self, guild_id: int, config: dict):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.config = config
        self._setup_buttons()

    def _setup_buttons(self):
        enabled = self.config.get("enabled_buttons", ["verify", "rules", "ticket", "info", "optout"])

        # Mapping of button keys to their implementation
        if "verify" in enabled:
            btn = ui.Button(label="✅ Verify Now", style=discord.ButtonStyle.success, custom_id=f"wdm_verify_{self.guild_id}")
            btn.callback = self.jump_to_verify
            self.add_item(btn)

        if "rules" in enabled:
            btn = ui.Button(label="📜 Read Rules", style=discord.ButtonStyle.primary, custom_id=f"wdm_rules_{self.guild_id}")
            btn.callback = self.jump_to_rules
            self.add_item(btn)

        if "roles" in enabled:
            btn = ui.Button(label="🎭 Pick Roles", style=discord.ButtonStyle.primary, custom_id=f"wdm_roles_{self.guild_id}")
            btn.callback = self.jump_to_roles
            self.add_item(btn)

        if "ticket" in enabled:
            btn = ui.Button(label="🎫 Open Ticket", style=discord.ButtonStyle.secondary, custom_id=f"wdm_ticket_{self.guild_id}")
            btn.callback = self.open_ticket
            self.add_item(btn)

        if "apply" in enabled:
            btn = ui.Button(label="📋 Apply for Staff", style=discord.ButtonStyle.secondary, custom_id=f"wdm_apply_{self.guild_id}")
            btn.callback = self.apply_staff
            self.add_item(btn)

        if "help" in enabled:
            btn = ui.Button(label="🆘 Get Help", style=discord.ButtonStyle.danger, custom_id=f"wdm_help_{self.guild_id}")
            btn.callback = self.get_help
            self.add_item(btn)

        if "info" in enabled:
            btn = ui.Button(label="📊 Server Info", style=discord.ButtonStyle.secondary, custom_id=f"wdm_info_{self.guild_id}")
            btn.callback = self.server_info
            self.add_item(btn)

        if "optout" in enabled:
            btn = ui.Button(label="🔕 Opt Out of DMs", style=discord.ButtonStyle.grey, custom_id=f"wdm_optout_{self.guild_id}")
            btn.callback = self.opt_out
            self.add_item(btn)

    async def jump_to_verify(self, i: discord.Interaction):
        guild = i.client.get_guild(self.guild_id)
        if not guild: return await i.response.send_message("Guild not found.", ephemeral=True)
        ch = discord.utils.get(guild.text_channels, name="verify")
        msg = f"Go to {ch.mention} to verify!" if ch else "Verification channel not found."
        await i.response.send_message(msg, ephemeral=True)

    async def jump_to_rules(self, i: discord.Interaction):
        guild = i.client.get_guild(self.guild_id)
        if not guild: return
        ch = discord.utils.get(guild.text_channels, name="rules")
        await i.response.send_message(f"Read rules in {ch.mention}" if ch else "Rules channel not found.", ephemeral=True)

    async def jump_to_roles(self, i: discord.Interaction):
        await i.response.send_message("Please pick your roles in the role selection channel on the server.", ephemeral=True)

    async def open_ticket(self, i: discord.Interaction):
        from modules.tickets import TicketModal
        await i.response.send_modal(TicketModal(i.client))

    async def apply_staff(self, i: discord.Interaction):
        cid = self.config.get("apply_channel")
        guild = i.client.get_guild(self.guild_id)
        ch = guild.get_channel(cid) if cid and guild else None
        await i.response.send_message(f"Apply here: {ch.mention}" if ch else "Application channel not configured.", ephemeral=True)

    async def get_help(self, i: discord.Interaction):
        role_id = self.config.get("help_ping_role")
        guild = i.client.get_guild(self.guild_id)
        if guild:
            role = guild.get_role(role_id) if role_id else None
            log_ch = discord.utils.get(guild.text_channels, name="staff-logs")
            if log_ch:
                await log_ch.send(f"🆘 **Help Requested**\nUser: {i.user.mention} requested help via Welcome DM. {role.mention if role else ''}")
        await i.response.send_message("Staff have been notified of your request.", ephemeral=True)

    async def server_info(self, i: discord.Interaction):
        guild = i.client.get_guild(self.guild_id)
        content = self.config.get("info_content")
        if not content:
            content = f"**Server Name:** {guild.name if guild else 'Unknown'}\n**Members:** {guild.member_count if guild else '?'}\n**Rules:** Please respect everyone."

        embed = discord.Embed(title="Server Information", description=content, color=discord.Color.blue())
        await i.response.send_message(embed=embed, ephemeral=True)

    async def opt_out(self, i: discord.Interaction):
        pref = dm.load_json("dm_opt_out", default=[])
        if i.user.id not in pref:
            pref.append(i.user.id)
            dm.save_json("dm_opt_out", pref)
        await i.response.send_message("✅ You have opted out of future welcome DMs from this bot.", ephemeral=True)

class WelcomeDMSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "welcomedm_config", {
            "enabled": True,
            "dm_message": "Welcome to **{server}**! We are glad to have you here.",
            "dm_color": "#5865F2",
            "enabled_buttons": ["verify", "rules", "ticket", "info", "optout"],
            "help_ping_role": None,
            "info_content": None,
            "apply_channel": None,
            "stats": {"sent": 0, "opted_out": 0, "verified_clicks": 0}
        })

    async def send_welcome_dm(self, member: discord.Member):
        opt_out = dm.load_json("dm_opt_out", default=[])
        if member.id in opt_out: return

        guild = member.guild
        config = self.get_config(guild.id)
        if not config.get("enabled", True): return

        color_hex = config.get("dm_color", "#5865F2").replace("#", "")
        try:
            color = discord.Color(int(color_hex, 16))
        except:
            color = discord.Color.blue()

        embed = discord.Embed(
            title=f"Welcome to {guild.name}!",
            description=config.get("dm_message").replace("{server}", guild.name).replace("{user}", member.name),
            color=color
        )
        if guild.icon:
            embed.set_author(name=guild.name, icon_url=guild.icon.url)
        embed.add_field(name="Member Count", value=str(guild.member_count))

        view = WelcomeDMView(guild.id, config)
        try:
            await member.send(embed=embed, view=view)
            config["stats"]["sent"] = config["stats"].get("sent", 0) + 1
            dm.update_guild_data(guild.id, "welcomedm_config", config)
        except discord.Forbidden:
            pass

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        config = self.get_config(guild.id)
        config["enabled"] = True
        dm.update_guild_data(guild.id, "welcomedm_config", config)

        # Guide channel
        guide = discord.utils.get(guild.text_channels, name="welcomedm-guide") or await guild.create_text_channel("welcomedm-guide")
        embed = discord.Embed(title="📩 Welcome DM System Guide", description="New members will receive a DM with interactive buttons upon joining.", color=discord.Color.blue())
        embed.add_field(name="Commands", value="`!welcomedmpanel` - Open admin panel\n`!help welcomedm` - Show this guide")
        await guide.send(embed=embed)

        await interaction.followup.send(f"✅ Welcome DM system enabled. Guide: {guide.mention}")
        return True
