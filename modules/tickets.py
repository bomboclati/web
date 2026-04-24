import discord
from discord.ext import commands
import asyncio
import json
import time
import io
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone, timedelta

from data_manager import dm
from logger import logger
from vector_memory import vector_memory


class TicketStatus(Enum):
    OPEN = "open"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ESCALATED = "escalated"


class TicketPriority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4


class TicketCategory(Enum):
    GENERAL = "general"
    SUPPORT = "support"
    REPORT = "report"
    APPEAL = "appeal"
    SUGGESTION = "suggestion"
    TECHNICAL = "technical"
    BILLING = "billing"


@dataclass
class Ticket:
    id: str
    guild_id: int
    channel_id: int
    user_id: int
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    title: str
    messages: List[dict]
    created_at: float
    updated_at: float
    assigned_to: Optional[int]
    sentiment_score: float
    sentiment_label: str


class TicketModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Open a Support Ticket")

        self.subject = discord.ui.TextInput(
            label="Subject",
            placeholder="What is your ticket about?",
            required=True,
            max_length=100
        )
        self.description = discord.ui.TextInput(
            label="Description",
            placeholder="Please provide details about your issue...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.priority = discord.ui.TextInput(
            label="Priority",
            placeholder="Low, Medium, or High",
            required=True,
            min_length=3,
            max_length=6
        )

        self.add_item(self.subject)
        self.add_item(self.description)
        self.add_item(self.priority)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        priority_map = {
            "low": TicketPriority.LOW,
            "medium": TicketPriority.MEDIUM,
            "high": TicketPriority.HIGH
        }
        priority = priority_map.get(self.priority.value.lower(), TicketPriority.MEDIUM)

        system = getattr(interaction.client, "tickets", None)
        if system:
            await system.create_ticket_from_modal(
                interaction,
                self.subject.value,
                self.description.value,
                priority
            )
        else:
            await interaction.followup.send("❌ Ticket system not found.", ephemeral=True)

class TicketOpenPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.success, custom_id="ticket_open_btn")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if not system: return

        # Check max per user
        settings = system.get_guild_settings(interaction.guild_id)
        max_per_user = settings.get("max_per_user", 0)

        if max_per_user > 0:
            open_tickets = system.get_user_tickets(interaction.guild_id, interaction.user.id)
            if len(open_tickets) >= max_per_user:
                return await interaction.response.send_message(f"❌ You already have {len(open_tickets)} open tickets. Please close one before opening another.", ephemeral=True)

        await interaction.response.send_modal(TicketModal())

class TicketPersistentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.danger, custom_id="ticket_close_v3", row=0)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_close_ticket(interaction)

    @discord.ui.button(label="📋 Transcript", style=discord.ButtonStyle.secondary, custom_id="ticket_transcript_v3", row=0)
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_transcript(interaction)

    @discord.ui.button(label="👤 Add", style=discord.ButtonStyle.primary, custom_id="ticket_add_user_v3", row=0)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_UserIDModal("Add User to Ticket", "add"))

    @discord.ui.button(label="🚫 Remove", style=discord.ButtonStyle.primary, custom_id="ticket_remove_user_v3", row=0)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_UserIDModal("Remove User from Ticket", "remove"))

    @discord.ui.button(label="✋ Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim_v3", row=1)
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_claim_ticket(interaction)

    @discord.ui.button(label="🔁 Unclaim", style=discord.ButtonStyle.secondary, custom_id="ticket_unclaim_v3", row=1)
    async def unclaim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_unclaim_ticket(interaction)

    @discord.ui.button(label="⬆️ Escalate", style=discord.ButtonStyle.danger, custom_id="ticket_escalate_v3", row=1)
    async def escalate(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_escalate(interaction)

    @discord.ui.button(label="📌 Pin", style=discord.ButtonStyle.secondary, custom_id="ticket_pin_v3", row=1)
    async def pin_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(_MessageIDModal())

    @discord.ui.button(label="❗ Resolve", style=discord.ButtonStyle.success, custom_id="ticket_resolve_v3", row=2)
    async def mark_resolved(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_mark_resolved(interaction)

    @discord.ui.button(label="🔓 Reopen", style=discord.ButtonStyle.primary, custom_id="ticket_reopen_v3", row=2)
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, "tickets", None)
        if system: await system.handle_reopen(interaction)


class AdvancedTickets:
    def __init__(self, bot):
        self.bot = bot
        self._active_tickets: Dict[str, Ticket] = {}
        self._ticket_channels: Dict[int, int] = {}
        self._load_tickets()

    def _load_tickets(self):
        tickets_data = dm.load_json("tickets", default={})
        
        for ticket_id, data in tickets_data.items():
            try:
                ticket = Ticket(
                    id=ticket_id,
                    guild_id=data["guild_id"],
                    channel_id=data["channel_id"],
                    user_id=data["user_id"],
                    category=TicketCategory(data["category"]),
                    priority=TicketPriority(data["priority"]),
                    status=TicketStatus(data["status"]),
                    title=data["title"],
                    messages=data.get("messages", []),
                    created_at=data["created_at"],
                    updated_at=data["updated_at"],
                    assigned_to=data.get("assigned_to"),
                    sentiment_score=data.get("sentiment_score", 0.5),
                    sentiment_label=data.get("sentiment_label", "neutral")
                )
                self._active_tickets[ticket_id] = ticket
                self._ticket_channels[ticket.channel_id] = ticket_id
            except Exception as e:
                logger.error(f"Failed to load ticket {ticket_id}: {e}")

    def _save_ticket(self, ticket: Ticket):
        tickets_data = dm.load_json("tickets", default={})
        tickets_data[ticket.id] = {
            "guild_id": ticket.guild_id,
            "channel_id": ticket.channel_id,
            "user_id": ticket.user_id,
            "category": ticket.category.value,
            "priority": ticket.priority.value,
            "status": ticket.status.value,
            "title": ticket.title,
            "messages": ticket.messages,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
            "assigned_to": ticket.assigned_to,
            "sentiment_score": ticket.sentiment_score,
            "sentiment_label": ticket.sentiment_label
        }
        dm.save_json("tickets", tickets_data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "tickets_config", {
            "enabled": True,
            "staff_role_id": None,
            "category_id": None,
            "log_channel_id": None,
            "max_per_user": 3,
            "auto_close_hours": 24,
            "panel_title": "Support Tickets",
            "panel_description": "Click below to open a ticket.",
            "panel_color": 0x3498db,
            "senior_staff_role_id": None,
            "opener_dm_enabled": True
        })

    def get_user_tickets(self, guild_id: int, user_id: int) -> List[Ticket]:
        return [t for t in self._active_tickets.values()
                if t.guild_id == guild_id and t.user_id == user_id
                and t.status not in [TicketStatus.CLOSED]]

    def get_ticket_by_channel(self, channel_id: int) -> Optional[Ticket]:
        ticket_id = self._ticket_channels.get(channel_id)
        return self._active_tickets.get(ticket_id)

    async def create_ticket_from_modal(self, interaction: discord.Interaction, subject, description, priority):
        guild = interaction.guild
        settings = self.get_guild_settings(guild.id)
        
        # Determine category
        category_id = settings.get("category_id")
        category = guild.get_channel(int(category_id)) if category_id else None
        
        # Ticket number
        stats = dm.get_guild_data(guild.id, "ticket_stats", {"total": 0, "open": 0})
        stats["total"] += 1
        stats["open"] += 1
        dm.update_guild_data(guild.id, "ticket_stats", stats)

        # Create channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        
        staff_role_id = settings.get("staff_role_id")
        if staff_role_id:
            staff_role = guild.get_role(int(staff_role_id))
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel_name = f"ticket-{interaction.user.name}-{stats['total']}"
        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        
        # Create Ticket object
        ticket_id = f"ticket_{guild.id}_{stats['total']}"
        ticket = Ticket(
            id=ticket_id,
            guild_id=guild.id,
            channel_id=channel.id,
            user_id=interaction.user.id,
            category=TicketCategory.SUPPORT,
            priority=priority,
            status=TicketStatus.OPEN,
            title=subject,
            messages=[],
            created_at=time.time(),
            updated_at=time.time(),
            assigned_to=None,
            sentiment_score=0.5,
            sentiment_label="neutral"
        )
        
        self._active_tickets[ticket_id] = ticket
        self._ticket_channels[channel.id] = ticket_id
        self._save_ticket(ticket)
        
        # Send initial message
        embed = discord.Embed(
            title=f"Ticket #{stats['total']}: {subject}",
            description=description,
            color=0x3498db
        )
        embed.add_field(name="Opener", value=interaction.user.mention, inline=True)
        embed.add_field(name="Priority", value=priority.name, inline=True)
        embed.set_footer(text="Staff will be with you shortly.")
        
        view = TicketPersistentView()
        await channel.send(embed=embed, view=view)
        
        if staff_role_id:
            await channel.send(f"<@&{staff_role_id}> New ticket created!")

        await interaction.followup.send(f"✅ Ticket created! Go to {channel.mention}", ephemeral=True)

    async def handle_claim_ticket(self, interaction: discord.Interaction):
        ticket = self.get_ticket_by_channel(interaction.channel_id)
        if not ticket: return
        
        ticket.assigned_to = interaction.user.id
        ticket.status = TicketStatus.IN_PROGRESS
        self._save_ticket(ticket)

        await interaction.response.send_message(f"✋ Ticket claimed by {interaction.user.mention}.", ephemeral=False)
        try:
            await interaction.channel.edit(name=f"claimed-{interaction.user.name}")
        except:
            pass

    async def handle_unclaim_ticket(self, interaction: discord.Interaction):
        ticket = self.get_ticket_by_channel(interaction.channel_id)
        if not ticket: return
        
        ticket.assigned_to = None
        ticket.status = TicketStatus.OPEN
        self._save_ticket(ticket)
        
        await interaction.response.send_message("🔁 Ticket unclaimed.", ephemeral=False)

    async def handle_mark_resolved(self, interaction: discord.Interaction):
        ticket = self.get_ticket_by_channel(interaction.channel_id)
        if not ticket: return

        if hasattr(self.bot, 'staff_shift'):
            await self.bot.staff_shift.track_ticket_resolved(interaction.guild_id, interaction.user.id)

        ticket.status = TicketStatus.RESOLVED
        self._save_ticket(ticket)
        
        embed = discord.Embed(
            title="✅ Ticket Resolved",
            description="This ticket has been marked as resolved. You can close it or reopen if needed.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    async def handle_reopen(self, interaction: discord.Interaction):
        ticket = self.get_ticket_by_channel(interaction.channel_id)
        if not ticket: return
        
        ticket.status = TicketStatus.OPEN
        self._save_ticket(ticket)
        
        await interaction.response.send_message("🔓 Ticket reopened.", ephemeral=False)

    async def handle_close_ticket(self, interaction: discord.Interaction):
        ticket = self.get_ticket_by_channel(interaction.channel_id)
        if not ticket: return
        
        await interaction.response.send_message("🔒 Closing ticket in 10 seconds... Generating transcript.", ephemeral=False)
        
        # Generate and send transcript
        transcript_file = await self.generate_transcript(interaction.channel)
        
        settings = self.get_guild_settings(interaction.guild_id)
        log_channel_id = settings.get("log_channel_id")
        if log_channel_id:
            log_channel = interaction.guild.get_channel(int(log_channel_id))
            if log_channel:
                embed = discord.Embed(title="📁 Ticket Transcript", description=f"Ticket: {ticket.title}\nUser: <@{ticket.user_id}>", color=0x3498db)
                await log_channel.send(embed=embed, file=discord.File(transcript_file, filename=f"transcript-{interaction.channel.name}.txt"))
        
        # DM opener
        if settings.get("opener_dm_enabled", True):
            try:
                opener = await interaction.guild.fetch_member(ticket.user_id)
                if opener:
                    transcript_file.seek(0)
                    await opener.send(content=f"Your ticket '{ticket.title}' in {interaction.guild.name} has been closed. Here is your transcript:", file=discord.File(transcript_file, filename="transcript.txt"))
            except:
                pass
        
        ticket.status = TicketStatus.CLOSED
        self._save_ticket(ticket)
        
        stats = dm.get_guild_data(interaction.guild_id, "ticket_stats", {"total": 0, "open": 1, "closed": 0})
        stats["open"] = max(0, stats.get("open", 1) - 1)
        stats["closed"] = stats.get("closed", 0) + 1
        dm.update_guild_data(interaction.guild_id, "ticket_stats", stats)
        
        await asyncio.sleep(10)
        await interaction.channel.delete()

    async def handle_transcript(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        file = await self.generate_transcript(interaction.channel)
        await interaction.followup.send("📋 Transcript generated:", file=discord.File(file, filename="transcript.txt"), ephemeral=True)

    async def generate_transcript(self, channel):
        messages = []
        async for msg in channel.history(limit=None, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.clean_content
            messages.append(f"[{ts}] {msg.author.name}: {content}")
        
        transcript_text = "\n".join(messages)
        return io.BytesIO(transcript_text.encode('utf-8'))

    async def handle_escalate(self, interaction: discord.Interaction):
        class EscalationSelect(discord.ui.Select):
            def __init__(self):
                options = [
                    discord.SelectOption(label="Technical Issues", value="tech"),
                    discord.SelectOption(label="Billing Query", value="billing"),
                    discord.SelectOption(label="Abuse/Report", value="abuse"),
                    discord.SelectOption(label="Other", value="other")
                ]
                super().__init__(placeholder="Reason for escalation...", options=options)

            async def callback(self, it: discord.Interaction):
                system = getattr(it.client, "tickets", None)
                if not system: return
                ticket = system.get_ticket_by_channel(it.channel_id)
                if not ticket: return
                ticket.status = TicketStatus.ESCALATED
                ticket.priority = TicketPriority.URGENT
                system._save_ticket(ticket)
                settings = system.get_guild_settings(it.guild_id)
                senior_role_id = settings.get("senior_staff_role_id")
                ping = f"<@&{senior_role_id}> " if senior_role_id else ""
                await it.response.send_message(f"🚨 {ping}**Ticket Escalated!** Reason: {self.values[0]}", ephemeral=False)

        view = discord.ui.View()
        view.add_item(EscalationSelect())
        await interaction.response.send_message("Please select a reason for escalation:", view=view, ephemeral=True)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        return True

class _UserIDModal(discord.ui.Modal):
    def __init__(self, title, action_type):
        super().__init__(title=title)
        self.action_type = action_type
        self.user_input = discord.ui.TextInput(label="User ID or Mention", required=True)
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        user_id_str = self.user_input.value.strip("<@!>")
        try:
            user = await interaction.guild.fetch_member(int(user_id_str))
            if self.action_type == "add":
                await interaction.channel.set_permissions(user, view_channel=True, send_messages=True)
                await interaction.response.send_message(f"✅ Added {user.mention} to ticket.", ephemeral=True)
            else:
                await interaction.channel.set_permissions(user, overwrite=None)
                await interaction.response.send_message(f"✅ Removed {user.mention} from ticket.", ephemeral=True)
        except:
            await interaction.response.send_message("❌ User not found.", ephemeral=True)

class _MessageIDModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Pin Message")
        self.message_input = discord.ui.TextInput(label="Message ID", required=True)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            msg = await interaction.channel.fetch_message(int(self.message_input.value))
            await msg.pin()
            await interaction.response.send_message("📌 Message pinned.", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Message not found.", ephemeral=True)

from discord import app_commands
