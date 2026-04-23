import discord
from discord.ext import commands
from discord import ui
import asyncio
import time
import random
import re
import io
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger

class TicketModal(ui.Modal, title="🎫 Open a Support Ticket"):
    subject = ui.TextInput(label="Subject", placeholder="Briefly describe your issue", required=True, max_length=100)
    description = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="Provide more details...", required=True, max_length=1000)
    priority = ui.TextInput(label="Priority (Low/Medium/High)", placeholder="Medium", required=False, max_length=10)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        from modules.tickets import AdvancedTickets
        tsys = AdvancedTickets(self.bot)
        await tsys._handle_ticket_creation(i, self.subject.value, self.description.value, self.priority.value)

class TicketControlView(ui.View):
    """The buttons inside a ticket channel."""
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def _is_staff(self, i: discord.Interaction) -> bool:
        config = dm.get_guild_data(i.guild_id, "tickets_config", {})
        role_id = config.get("staff_role_id")
        if not role_id: return i.user.guild_permissions.manage_channels
        role = i.guild.get_role(role_id)
        return role in i.user.roles if role else i.user.guild_permissions.manage_channels

    @ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="tk_ctrl_close")
    async def close(self, i, b):
        await i.response.send_message("🔒 Ticket closing in 10 seconds. Saving transcript...", ephemeral=False)
        
        # Transcript logic
        transcript = await self.generate_transcript(i.channel)
        config = dm.get_guild_data(i.guild_id, "tickets_config", {})
        log_ch_id = config.get("log_channel_id")
        log_ch = i.guild.get_channel(log_ch_id) if log_ch_id else None

        file = discord.File(io.BytesIO(transcript.encode()), filename=f"transcript-{i.channel.name}.txt")
        if log_ch:
            await log_ch.send(f"📋 **Ticket Closed:** {i.channel.name}\nOpener ID: {i.channel.topic.split('Opener: ')[1] if 'Opener: ' in i.channel.topic else 'Unknown'}", file=file)

        # Opener DM
        if config.get("opener_dm"):
            opener_id = i.channel.topic.split('Opener: ')[1] if 'Opener: ' in (i.channel.topic or "") else None
            if opener_id:
                opener = i.guild.get_member(int(opener_id))
                if opener:
                    try: await opener.send(f"Your ticket **{i.channel.name}** was closed. Attached is your transcript.", file=discord.File(io.BytesIO(transcript.encode()), filename="transcript.txt"))
                    except: pass

        await asyncio.sleep(10)
        
        # Cleanup state
        if 'Opener: ' in (i.channel.topic or ""):
            uid = i.channel.topic.split('Opener: ')[1]
            if uid in config["open_tickets"]:
                config["open_tickets"][uid] -= 1
                if config["open_tickets"][uid] <= 0: del config["open_tickets"][uid]
        config["stats"]["closed"] = config["stats"].get("closed", 0) + 1
        dm.update_guild_data(i.guild_id, "tickets_config", config)
        
        try: await i.channel.delete(reason=f"Ticket closed by {i.user}")
        except: pass

    async def generate_transcript(self, channel: discord.TextChannel) -> str:
        messages = []
        async for msg in channel.history(limit=None, oldest_first=True):
            messages.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author.name}: {msg.content}")
        return "\n".join(messages)

    @ui.button(label="📋 Transcript", style=discord.ButtonStyle.secondary, custom_id="tk_ctrl_trans")
    async def transcript_btn(self, i, b):
        t = await self.generate_transcript(i.channel)
        file = discord.File(io.BytesIO(t.encode()), filename="transcript.txt")
        await i.response.send_message("✅ Transcript generated.", file=file, ephemeral=True)

    @ui.button(label="✋ Claim", style=discord.ButtonStyle.success, custom_id="tk_ctrl_claim")
    async def claim(self, i, b):
        if not await self._is_staff(i): return await i.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)
        embed = i.message.embeds[0]
        embed.set_footer(text=f"Claimed by {i.user.display_name}")
        await i.message.edit(embed=embed)
        await i.response.send_message(f"✋ Ticket claimed by {i.user.mention}", ephemeral=False)

    @ui.button(label="🔁 Unclaim", style=discord.ButtonStyle.secondary, custom_id="tk_ctrl_unclaim")
    async def unclaim(self, i, b):
        if not await self._is_staff(i): return await i.response.send_message("❌ Only staff can unclaim tickets.", ephemeral=True)
        embed = i.message.embeds[0]
        embed.set_footer(text=None)
        await i.message.edit(embed=embed)
        await i.response.send_message("🔁 Ticket unclaimed.", ephemeral=False)

    @ui.button(label="👤 Add User", style=discord.ButtonStyle.primary, custom_id="tk_ctrl_add")
    async def add_u(self, i, b):
        class AddModal(ui.Modal, title="Add User to Ticket"):
            uid = ui.TextInput(label="User ID or Mention")
            async def on_submit(self, it):
                raw = self.uid.value.strip().replace("<@!", "").replace("<@", "").replace(">", "")
                try:
                    target = it.guild.get_member(int(raw))
                    if target:
                        await it.channel.set_permissions(target, view_channel=True, send_messages=True)
                        await it.response.send_message(f"✅ Added {target.mention} to the ticket.", ephemeral=True)
                    else: raise ValueError
                except: await it.response.send_message("❌ User not found.", ephemeral=True)
        await i.response.send_modal(AddModal())

    @ui.button(label="🚫 Remove User", style=discord.ButtonStyle.primary, custom_id="tk_ctrl_rem")
    async def rem_u(self, i, b):
        class RemModal(ui.Modal, title="Remove User from Ticket"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                try:
                    target = it.guild.get_member(int(self.uid.value))
                    if target:
                        await it.channel.set_permissions(target, overwrite=None)
                        await it.response.send_message(f"✅ Removed {target.mention} from the ticket.", ephemeral=True)
                except: await it.response.send_message("❌ User not found.", ephemeral=True)
        await i.response.send_modal(RemModal())

    @ui.button(label="⬆️ Escalate", style=discord.ButtonStyle.secondary, custom_id="tk_ctrl_esc")
    async def escalate(self, i, b):
        view = ui.View()
        select = ui.Select(placeholder="Reason for escalation", options=[
            discord.SelectOption(label="Senior Staff Needed", value="senior"),
            discord.SelectOption(label="Complex Issue", value="complex"),
            discord.SelectOption(label="Payment Dispute", value="billing")
        ])
        async def callback(it):
            embed = i.message.embeds[0]
            embed.color = discord.Color.dark_red()
            embed.add_field(name="Escalation Status", value=f"Escalated for: {select.values[0]}", inline=False)
            await i.message.edit(embed=embed)
            await it.response.send_message("⬆️ Ticket escalated. Senior staff notified.", ephemeral=False)
        select.callback = callback; view.add_item(select); await i.response.send_message("Choose reason:", view=view, ephemeral=True)

    @ui.button(label="📌 Pin Msg", style=discord.ButtonStyle.secondary, custom_id="tk_ctrl_pin")
    async def pin_msg(self, i, b):
        class PinModal(ui.Modal, title="Pin Message"):
            mid = ui.TextInput(label="Message ID")
            async def on_submit(self, it):
                try:
                    msg = await it.channel.fetch_message(int(self.mid.value))
                    await msg.pin()
                    await it.response.send_message("📌 Message pinned.", ephemeral=True)
                except: await it.response.send_message("❌ Message not found.", ephemeral=True)
        await i.response.send_modal(PinModal())

    @ui.button(label="❗ Resolved", style=discord.ButtonStyle.success, custom_id="tk_ctrl_res")
    async def resolve(self, i, b):
        embed = discord.Embed(title="✅ Ticket Resolved", description="This issue has been marked as resolved by staff. If you have further questions, please open a new ticket.", color=discord.Color.green())
        await i.channel.send(embed=embed)
        await i.response.send_message("✅ Status updated.", ephemeral=True)

class AdvancedTickets:
    def __init__(self, bot):
        self.bot = bot

    def get_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "tickets_config", {
            "enabled": True,
            "staff_role_id": None,
            "category_id": None,
            "log_channel_id": None,
            "max_per_user": 1,
            "auto_close_hours": 0,
            "open_tickets": {}, # user_id: count
            "stats": {"total": 0, "closed": 0},
            "opener_dm": True
        })

    async def _handle_ticket_creation(self, i: discord.Interaction, subject: str, desc: str, priority: str):
        guild = i.guild
        config = self.get_config(guild.id)
        
        user_tickets = config["open_tickets"].get(str(i.user.id), 0)
        if config["max_per_user"] > 0 and user_tickets >= config["max_per_user"]:
             return await i.followup.send(f"❌ You have reached the limit of {config['max_per_user']} open tickets.", ephemeral=True)

        cat = guild.get_channel(config["category_id"]) if config["category_id"] else None
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            i.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
        }
        staff_role = guild.get_role(config["staff_role_id"]) if config["staff_role_id"] else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)

        ticket_ch = await guild.create_text_channel(
            name=f"ticket-{i.user.name}-{random.randint(1000, 9999)}",
            category=cat,
            overwrites=overwrites,
            topic=f"Subject: {subject} | Opener: {i.user.id}"
        )

        config["open_tickets"][str(i.user.id)] = user_tickets + 1
        config["stats"]["total"] += 1
        dm.update_guild_data(guild.id, "tickets_config", config)

        embed = discord.Embed(title=f"Ticket: {subject}", description=desc, color=discord.Color.blue())
        embed.add_field(name="User", value=i.user.mention, inline=True)
        embed.add_field(name="Priority", value=priority or "Medium", inline=True)
        embed.set_footer(text=f"Ticket ID: {ticket_ch.id}")

        await ticket_ch.send(content=f"{i.user.mention} {staff_role.mention if staff_role else ''}", embed=embed, view=TicketControlView(guild.id))
        await i.followup.send(f"✅ Ticket created: {ticket_ch.mention}", ephemeral=True)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        config = self.get_config(guild.id)
        ch = discord.utils.get(guild.text_channels, name="open-ticket") or await guild.create_text_channel("open-ticket")
        from modules.config_panels import TicketOpenButton
        embed = discord.Embed(title="🎫 Support Tickets", description="Click the button below to open a ticket.", color=discord.Color.green())
        await ch.send(embed=embed, view=TicketOpenButton())
        config["enabled"] = True
        dm.update_guild_data(guild.id, "tickets_config", config)
        await interaction.followup.send(f"✅ Ticket system ready in {ch.mention}!")
        return True
