import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

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


@dataclass
class TicketResponse:
    trigger: str
    response: str
    category: TicketCategory


class AdvancedTickets:
    """Ticket system with templates and canned responses."""
    
    TICKET_TEMPLATES = {
        "bug_report": {
            "name": "Bug Report",
            "category": "technical",
            "priority": "high",
            "title": "Bug Report: [Title]",
            "questions": [
                "What were you doing when the bug occurred?",
                "What did you expect to happen?",
                "What actually happened?",
                "Any error messages?",
                "Steps to reproduce:"
            ]
        },
        "feature_request": {
            "name": "Feature Request",
            "category": "suggestion",
            "priority": "medium",
            "title": "Feature Request: [Title]",
            "questions": [
                "What feature would you like?",
                "Why do you need this feature?",
                "How would you use it?",
                "Any alternative solutions?"
            ]
        },
        "billing": {
            "name": "Billing Issue",
            "category": "billing",
            "priority": "high",
            "title": "Billing: [Issue]",
            "questions": [
                "What is your billing concern?",
                "What card/email used?",
                "Expected vs actual charge?",
                "Refund request?"
            ]
        },
        "account": {
            "name": "Account Help",
            "category": "support",
            "priority": "medium",
            "title": "Account: [Issue]",
            "questions": [
                "What issue are you having?",
                "Email associated with account?",
                "Any error messages?",
                " Tried troubleshooting?"
            ]
        },
        "general": {
            "name": "General Support",
            "category": "general", 
            "priority": "low",
            "title": "Support: [Topic]",
            "questions": [
                "How can we help you today?",
                "Any relevant details?",
                "Links to related info?"
            ]
        }
    }
    
    CANNED_RESPONSES = {
        "greeting": "Hi! Thanks for creating a ticket. We'll help you shortly.",
        "thanks": "Thank you for your patience while we look into this.",
        "more_info": "Could you provide more details about this?",
        "resolved": "Glad we could help! Is there anything else you need?",
        "closing": "This ticket will be closed. Create a new one if you need more help!",
        "escalated": "Escalating this to our team. Theyll respond soon.",
        "wait": "Please allow us some time to investigate this."
    }
    
    def __init__(self, bot):
        self.bot = bot
        self._active_tickets: Dict[str, Ticket] = {}
        self._ticket_channels: Dict[int, int] = {}
        self._auto_responses: Dict[int, List[TicketResponse]] = {}
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
        return dm.get_guild_data(guild_id, "ticket_settings", {
            "enabled": True,
            "auto_category": True,
            "sentiment_analysis": True,
            "auto_responses": True,
            "escalation_channel": None,
            "staff_role": None,
            "categories": {
                "general": {"emoji": "💬", "color": "blue"},
                "support": {"emoji": "❓", "color": "green"},
                "report": {"emoji": "🚨", "color": "red"},
                "appeal": {"emoji": "📝", "color": "yellow"},
                "suggestion": {"emoji": "💡", "color": "purple"},
                "technical": {"emoji": "🔧", "color": "orange"},
                "billing": {"emoji": "💳", "color": "gold"}
            }
        })

    async def analyze_sentiment(self, message: str, guild_id: int) -> tuple:
        if not self.get_guild_settings(guild_id).get("sentiment_analysis", True):
            return 0.5, "neutral"
        
        prompt = f"""Analyze the sentiment of this support message.

MESSAGE: {message}

Respond with JSON only:
{{
    "sentiment_score": 0.0 to 1.0,
    "sentiment_label": "negative/neutral/positive",
    "urgency_level": "low/medium/high",
    "emotion": "frustrated/angry/sad/confused/hopeful/happy"
}}

Consider:
- All caps = frustrated/urgent
- Lots of punctuation!!! = strong emotion
- Words like "urgent", "asap", "now" = high urgency
- "please help", "thanks" = positive/polite
- "terrible", "worst", "hate" = negative"""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=0,
                user_input=prompt,
                system_prompt="You analyze support message sentiment. Be accurate and consider context."
            )
            
            score = float(result.get("sentiment_score", 0.5))
            label = result.get("sentiment_label", "neutral")
            
            priority = TicketPriority.LOW
            urgency = result.get("urgency_level", "low")
            if urgency == "high":
                priority = TicketPriority.HIGH
            elif urgency == "medium":
                priority = TicketPriority.MEDIUM
            
            emotion = result.get("emotion", "neutral")
            if emotion in ["frustrated", "angry"]:
                priority = TicketPriority(min(priority.value + 1, 4))
            
            return score, label, priority
            
        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            return 0.5, "neutral", TicketPriority.MEDIUM

    async def categorize_ticket(self, message: str, guild_id: int) -> TicketCategory:
        settings = self.get_guild_settings(guild_id)
        
        if not settings.get("auto_category", True):
            return TicketCategory.GENERAL
        
        prompt = f"""Categorize this support ticket.

MESSAGE: {message}

Available categories: general, support, report, appeal, suggestion, technical, billing

Respond with JSON only:
{{
    "category": "one of the categories",
    "reason": "brief explanation"
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=0,
                user_input=prompt,
                system_prompt="You categorize support tickets. Choose the most appropriate category."
            )
            
            category_str = result.get("category", "general")
            return TicketCategory(category_str)
            
        except Exception as e:
            logger.error(f"Ticket categorization failed: {e}")
            return TicketCategory.GENERAL

    async def create_ticket(self, guild_id: int, user_id: int, channel: discord.TextChannel, 
                         initial_message: str) -> Ticket:
        sentiment_score, sentiment_label, priority = await self.analyze_sentiment(initial_message, guild_id)
        category = await self.categorize_ticket(initial_message, guild_id)
        
        ticket_id = f"ticket_{guild_id}_{int(time.time())}"
        
        ticket = Ticket(
            id=ticket_id,
            guild_id=guild_id,
            channel_id=channel.id,
            user_id=user_id,
            category=category,
            priority=priority,
            status=TicketStatus.OPEN,
            title=initial_message[:100],
            messages=[{
                "role": "user",
                "content": initial_message,
                "timestamp": time.time(),
                "sentiment": sentiment_label
            }],
            created_at=time.time(),
            updated_at=time.time(),
            assigned_to=None,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label
        )
        
        self._active_tickets[ticket_id] = ticket
        self._ticket_channels[channel.id] = ticket_id
        self._save_ticket(ticket)
        
        assigned_staff = await self._assign_ticket_to_staff(guild, ticket)
        
        await self._send_ticket_created_message(ticket, channel, initial_message, assigned_staff)
        
        await self._check_auto_responses(ticket)
        
        vector_memory.store_conversation(
            guild_id=guild_id,
            user_id=user_id,
            user_message=f"TICKET: {initial_message[:200]}",
            bot_response=f"Ticket created - Category: {category.value}, Priority: {priority.name}",
            reasoning="Ticket system - initial message stored for learning",
            walkthrough="Creating ticket with AI categorization",
            importance_score=0.8
        )
        
        return ticket

    async def _assign_ticket_to_staff(self, guild: discord.Guild, ticket: Ticket) -> Optional[discord.Member]:
        """Find and assign ticket to available staff"""
        settings = self.get_guild_settings(guild.id)
        staff_role_ids = settings.get("staff_roles", [])
        
        if not staff_role_ids:
            return None
        
        staff_members = []
        for role_id in staff_role_ids:
            role = guild.get_role(role_id)
            if role:
                staff_members.extend(role.members)
        
        if not staff_members:
            return None
        
        counts = {}
        for member in staff_members:
            if member.bot:
                continue
            count = sum(1 for t in self._active_tickets.values() 
                        if t.staff_id == member.id and t.status == TicketStatus.OPEN)
            counts[member.id] = count
        
        if not counts:
            return None
        
        staff_id = min(counts, key=counts.get)
        staff = guild.get_member(staff_id)
        
        if staff:
            ticket.staff_id = staff_id
            ticket.assigned_to = staff_id
        
        return staff

    async def _send_ticket_created_message(self, ticket: Ticket, channel: discord.TextChannel, message: str, assigned_staff=None):
        settings = self.get_guild_settings(ticket.guild_id)
        cat_settings = settings.get("categories", {}).get(ticket.category.value, {})
        
        embed = discord.Embed(
            title=f"{cat_settings.get('emoji', '🎫')} Ticket Created",
            description=f"**{ticket.title}**",
            color=discord.Color.blue()
        )
        
        if assigned_staff:
            embed.add_field(name="Assigned To", value=assigned_staff.mention, inline=True)
            ping_msg = f"{assigned_staff.mention} 🎫 New ticket assigned to you!"
        else:
            ping_msg = ""
        
        priority_emoji = "🔴" if ticket.priority == TicketPriority.URGENT else "🟠" if ticket.priority == TicketPriority.HIGH else "🟡" if ticket.priority == TicketPriority.MEDIUM else "🟢"
        embed.add_field(name="Priority", value=f"{priority_emoji} {ticket.priority.name}", inline=True)
        
        embed.add_field(name="Status", value=ticket.status.value.title(), inline=True)
        embed.add_field(name="Sentiment", value=f"{ticket.sentiment_label} ({ticket.sentiment_score:.1f})", inline=True)
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id=f"ticket_close_{ticket.id}"))
        
        await channel.send(ping_msg, embed=embed, view=view)

    async def _check_auto_responses(self, ticket: Ticket):
        settings = self.get_guild_settings(ticket.guild_id)
        if not settings.get("auto_responses", True):
            return
        
        last_message = ticket.messages[-1]["content"] if ticket.messages else ""
        
        auto_responses = dm.get_guild_data(ticket.guild_id, "ticket_auto_responses", {})
        
        for trigger, response in auto_responses.items():
            if trigger.lower() in last_message.lower():
                await self.add_ticket_response(ticket.id, "assistant", response)
                break

    async def add_ticket_response(self, ticket_id: str, role: str, content: str):
        if ticket_id not in self._active_tickets:
            return
        
        ticket = self._active_tickets[ticket_id]
        ticket.messages.append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        ticket.updated_at = time.time()
        
        self._save_ticket(ticket)

async def resolve_ticket(self, ticket_id: str, resolution: str):
        if ticket_id not in self._active_tickets:
            return
        
        ticket = self._active_tickets[ticket_id]
        
        if ticket.status != TicketStatus.RESOLVED:
            ticket.status = TicketStatus.RESOLVED
            ticket.updated_at = time.time()
            
            ticket.messages.append({
                "role": "system",
                "content": f"RESOLVED: {resolution}",
                "timestamp": time.time()
            })
            
            self._save_ticket(ticket)
            
            staff_id = ticket.staff_id
            if staff_id:
                current = dm.get_guild_data(ticket.guild_id, f"tickets_resolved_{staff_id}", 0)
                dm.update_guild_data(ticket.guild_id, f"tickets_resolved_{staff_id}", current + 1)
        
        vector_memory.store_conversation(
            guild_id=ticket.guild_id,
            user_id=ticket.user_id,
            user_message=f"TICKET RESOLUTION: {ticket.title}",
            bot_response=resolution,
            reasoning="Ticket resolved - stored for learning",
            walkthrough="Marking ticket as resolved",
            importance_score=0.6
        )

    async def escalate_ticket(self, ticket_id: str, reason: str):
        if ticket_id not in self._active_tickets:
            return
        
        ticket = self._active_tickets[ticket_id]
        ticket.status = TicketStatus.ESCALATED
        ticket.priority = TicketPriority.URGENT
        ticket.updated_at = time.time()
        
        settings = self.get_guild_settings(ticket.guild_id)
        escalation_channel_id = settings.get("escalation_channel")
        
        if escalation_channel_id:
            guild = self.bot.get_guild(ticket.guild_id)
            escalation_channel = guild.get_channel(escalation_channel_id)
            
            if escalation_channel:
                embed = discord.Embed(
                    title="🚨 Ticket Escalated",
                    description=f"**{ticket.title}**",
                    color=discord.Color.red()
                )
                embed.add_field(name="User", value=f"<@{ticket.user_id}>", inline=True)
                embed.add_field(name="Category", value=ticket.category.value, inline=True)
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.add_field(name="Sentiment", value=f"{ticket.sentiment_label} ({ticket.sentiment_score:.1f})", inline=True)
                
                await escalation_channel.send(embed=embed)
        
        self._save_ticket(ticket)

    def get_user_tickets(self, guild_id: int, user_id: int) -> List[Ticket]:
        return [t for t in self._active_tickets.values() 
                if t.guild_id == guild_id and t.user_id == user_id 
                and t.status not in [TicketStatus.CLOSED, TicketStatus.RESOLVED]]

    def get_ticket_by_channel(self, channel_id: int) -> Optional[Ticket]:
        ticket_id = self._ticket_channels.get(channel_id)
        return self._active_tickets.get(ticket_id)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "ticket_settings", settings)
        
        # Create documentation channel
        try:
            doc_channel = await guild.create_text_channel("tickets-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        # Post comprehensive documentation
        doc_embed = discord.Embed(
            title="🎫 Advanced Ticket System Guide",
            description="Complete guide to using the AI-powered ticket system with sentiment analysis!",
            color=discord.Color.blue()
        )
        doc_embed.add_field(
            name="📖 How It Works",
            value="Create tickets by describing your issue. AI automatically categorizes it, analyzes sentiment (frustrated/happy), and assigns priority. Staff get notified and can respond.",
            inline=False
        )
        doc_embed.add_field(
            name="🎮 Available Commands",
            value="**!ticket <message>** - Create a new support ticket\n" +
                  "**!tickets** - List your active tickets\n" +
                  "**!close** - Close the current ticket\n" +
                  "**!help tickets** - Show this guide",
            inline=False
        )
        doc_embed.add_field(
            name="💡 How to Use",
            value="1. Type `!ticket I need help with...` describing your issue\n" +
                  "2. AI categorizes it (support/technical/billing/etc)\n" +
                  "3. Sentiment is analyzed - urgent issues get priority\n" +
                  "4. Staff get notified and respond\n" +
                  "5. Use `!close` when resolved",
            inline=False
        )
        doc_embed.add_field(
            name="🏷️ Ticket Categories",
            value="• **general** - General questions\n" +
                  "• **support** - Technical support\n" +
                  "• **report** - Report issues\n" +
                  "• **appeal** - Appeal decisions\n" +
                  "• **suggestion** - Make suggestions\n" +
                  "• **technical** - Tech help\n" +
                  "• **billing** - Payment issues",
            inline=False
        )
        doc_embed.set_footer(text="Created by Miro AI • Use !help tickets for more info")
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** Create a ticket with `!ticket <your message>`")
        
        help_embed = discord.Embed(
            title="🎫 Advanced Ticket System",
            description="AI-powered ticket system with sentiment analysis and auto-routing.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Users create tickets by describing their issue. AI automatically categorizes, analyzes sentiment, and assigns priority.",
            inline=False
        )
        help_embed.add_field(
            name="Features",
            value="• AI categorization\n• Sentiment analysis\n• Auto-responses\n• Escalation to staff\n• Vector memory learning",
            inline=False
        )
        help_embed.add_field(
            name="!ticket <message>",
            value="Create a new support ticket.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["ticket"] = json.dumps({
            "command_type": "create_ticket"
        })
        custom_cmds["tickets"] = json.dumps({
            "command_type": "list_tickets"
        })
        custom_cmds["close"] = json.dumps({
            "command_type": "close_ticket"
        })
        custom_cmds["help tickets"] = json.dumps({
            "command_type": "help_embed",
            "title": "🎫 Advanced Ticket System",
            "description": "AI-powered ticket system with sentiment analysis.",
            "fields": [
                {"name": "!ticket <message>", "value": "Create a new support ticket.", "inline": False},
                {"name": "!tickets", "value": "List your active tickets.", "inline": False},
                {"name": "!close", "value": "Close current ticket.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
