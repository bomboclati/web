import discord
from discord import ui
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from data_manager import dm
from logger import logger
import os
from modules.config_panels import ConfigPanelView


@dataclass
class Reminder:
    id: str
    user_id: int
    guild_id: int
    channel_id: Optional[int]
    message: str
    remind_at: float
    recurring: Optional[str]
    created_at: float


class SnoozeButton(ui.Button):
    def __init__(self, reminder_id: str):
        super().__init__(label="Snooze 10min", style=discord.ButtonStyle.secondary, emoji="⏰")
        self.reminder_id = reminder_id

    async def callback(self, interaction: discord.Interaction):
        rs = interaction.client.reminder_system
        if rs.snooze_reminder(self.reminder_id, 600):
            await interaction.response.send_message("✅ Reminder snoozed for 10 minutes!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not snooze this reminder.", ephemeral=True)


class DismissButton(ui.Button):
    def __init__(self, reminder_id: str):
        super().__init__(label="Dismiss", style=discord.ButtonStyle.danger, emoji="✅")
        self.reminder_id = reminder_id

    async def callback(self, interaction: discord.Interaction):
        rs = interaction.client.reminder_system
        if rs.cancel_reminder(self.reminder_id):
            await interaction.response.edit_message(view=None)
            await interaction.followup.send("✅ Reminder dismissed.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not dismiss this reminder.", ephemeral=True)


class ReminderView(ui.View):
    def __init__(self, reminder_id: str):
        super().__init__(timeout=None)
        self.add_item(SnoozeButton(reminder_id))
        self.add_item(DismissButton(reminder_id))


class RemindersPanelView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reminders")

    def get_config(self, guild_id: int = None) -> dict:
        return dm.get_guild_data(guild_id or self.guild_id, "reminders_config", {
            "enabled": True,
            "max_per_user": 10,
            "allow_dms": True,
            "fallback_channel": None
        })

    def save_config(self, config: dict, guild_id: int = None, client = None):
        dm.update_guild_data(guild_id or self.guild_id, "reminders_config", config)

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="⏰ Reminders System Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="DMs Allowed", value="✅ Yes" if c.get("allow_dms", True) else "❌ No", inline=True)
        embed.add_field(name="Max Per User", value=str(c.get("max_per_user", 10)), inline=True)
        embed.add_field(name="Fallback Channel", value=f"<#{c.get('fallback_channel')}>" if c.get('fallback_channel') else "_None_", inline=True)

        reminders = dm.get_guild_data(guild_id or self.guild_id, "reminders", {})
        embed.add_field(name="Active Reminders", value=str(len(reminders)), inline=True)
        return embed

    @ui.button(label="View All Active", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_remind_viewall")
    async def view_all(self, interaction: discord.Interaction, button: ui.Button):
        reminders = dm.get_guild_data(interaction.guild_id, "reminders", {})
        if isinstance(reminders, list):
            all_reminders = reminders
        else:
            all_reminders = list(reminders.values())
        active = [r for r in all_reminders if r.get("remind_at", 0) > time.time()]
        
        if not active:
            return await interaction.response.send_message("📭 No active reminders.", ephemeral=True)
        
        desc = ""
        for i, r in enumerate(list(active)[:10], 1):
            user = interaction.guild.get_member(r["user_id"])
            time_str = f"<t:{int(r['remind_at'])}:R>"
            user_mention = user.mention if user else f"<@{r['user_id']}>"
            desc += f"**{i}.** {user_mention}: {r['message'][:30]}... → {time_str}\n"
        
        embed = discord.Embed(title="📋 Active Reminders", description=desc, color=discord.Color.blue())
        embed.set_footer(text=f"Total: {len(active)} | Showing first 10")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_remind_stats")
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        reminders = dm.get_guild_data(interaction.guild_id, "reminders", {})
        if isinstance(reminders, list):
            all_reminders = reminders
        else:
            all_reminders = list(reminders.values())
        now = time.time()
        today_start = now - 86400
        week_start = now - 604800

        total_active = sum(1 for r in all_reminders if r.get("remind_at", 0) > now)
        set_today = sum(1 for r in all_reminders if r.get("created_at", 0) > today_start)
        sent_week = sum(1 for r in all_reminders if r.get("remind_at", 0) < now and r.get("remind_at", 0) > week_start)

        # Most active user
        user_counts = {}
        for r in all_reminders:
            uid = r.get("user_id")
            user_counts[uid] = user_counts.get(uid, 0) + 1
        most_active = max(user_counts.items(), key=lambda x: x[1])[0] if user_counts else None
        
        embed = discord.Embed(title="📊 Reminders Stats", color=discord.Color.green())
        embed.add_field(name="Total Active", value=str(total_active), inline=True)
        embed.add_field(name="Set Today", value=str(set_today), inline=True)
        embed.add_field(name="Sent This Week", value=str(sent_week), inline=True)
        if most_active:
            embed.add_field(name="Most Active User", value=f"<@{most_active}>", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Set Admin Reminder", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_remind_setadmin")
    async def set_admin(self, interaction: discord.Interaction, button: ui.Button):
        modal = AdminReminderModal(self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Clear Expired", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_remind_clearexp")
    async def clear_expired(self, interaction: discord.Interaction, button: ui.Button):
        reminders = dm.get_guild_data(interaction.guild_id, "reminders", {})
        now = time.time()
        expired = [k for k, v in reminders.items() if v.get("remind_at", 0) < now]
        
        for k in expired:
            del reminders[k]
        
        dm.update_guild_data(interaction.guild_id, "reminders", reminders)
        await interaction.response.send_message(f"✅ Cleared {len(expired)} expired reminders.", ephemeral=True)

    @ui.button(label="Max Per User", emoji="🔢", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_remind_maxper")
    async def max_per_user(self, interaction: discord.Interaction, button: ui.Button):
        modal = MaxRemindersModal(self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Toggle DMs", emoji="🔔", style=discord.ButtonStyle.success, row=1, custom_id="cfg_remind_toggledm")
    async def toggle_dms(self, interaction: discord.Interaction, button: ui.Button):
        c = self.get_config(interaction.guild_id)
        c["allow_dms"] = not c.get("allow_dms", True)
        self.save_config(c, interaction.guild_id, interaction.client)
        status = "✅" if c["allow_dms"] else "❌"
        await interaction.response.send_message(f"{status} Reminder DMs {'enabled' if c['allow_dms'] else 'disabled'}.", ephemeral=True)
        await self.update_panel(interaction)

    @ui.button(label="Set Fallback Channel", emoji="📣", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_remind_fallback")
    async def set_fallback(self, interaction: discord.Interaction, button: ui.Button):
        class ChannelSelect(ui.ChannelSelect):
            def __init__(self, panel):
                super().__init__(placeholder="Select fallback channel...", min_values=1, max_values=1)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                c = self.panel.get_config(inner_interaction.guild_id)
                c["fallback_channel"] = self.values[0].id
                self.panel.save_config(c, inner_interaction.guild_id, inner_interaction.client)
                await inner_interaction.response.send_message(f"✅ Fallback channel set to {self.values[0].mention}.", ephemeral=True)
                await self.panel.update_panel(inner_interaction)
        
        view = ui.View()
        view.add_item(ChannelSelect(self))
        await interaction.response.send_message("Select a fallback channel:", view=view, ephemeral=True)


class AdminReminderModal(ui.Modal, title="Set Admin Reminder"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.message_input = ui.TextInput(label="Message", style=discord.TextStyle.paragraph, required=True, max_length=500)
        self.time_input = ui.TextInput(label="Time (e.g., 1h, 30m, 2d)", required=True, max_length=50)
        self.channel_input = ui.TextInput(label="Channel ID (optional)", required=False, max_length=30)
        self.add_item(self.message_input)
        self.add_item(self.time_input)
        self.add_item(self.channel_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_input.value) if self.channel_input.value else None
            rs = interaction.client.reminder_system
            reminder = await rs.create_reminder(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=channel_id or interaction.channel_id,
                message=self.message_input.value,
                time_input=self.time_input.value
            )
            await interaction.response.send_message(f"✅ Reminder set for <t:{int(reminder.remind_at)}:R>!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


class MaxRemindersModal(ui.Modal, title="Max Reminders Per User"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.max_input = ui.TextInput(label="Max reminders per user (0 = unlimited)", required=True, max_length=10)
        self.add_item(self.max_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            max_val = int(self.max_input.value)
            c = self.panel.get_config(interaction.guild_id)
            c["max_per_user"] = max_val
            self.panel.save_config(c, interaction.guild_id, interaction.client)
            await interaction.response.send_message(f"✅ Max reminders set to {max_val}.", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)


class ReminderSystem:
    def __init__(self, bot):
        self.bot = bot
        self._reminders: Dict[str, Reminder] = {}
        self._load_reminders()

    def _load_reminders(self):
        """Load reminders from all guild-specific data files."""
        count = 0
        data_dir = "data"
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_id_str = filename[6:-5]
                        if not guild_id_str.isdigit(): continue
                        guild_id = int(guild_id_str)
                        guild_data = dm.load_json(filename[:-5], default={})
                        reminders_data = guild_data.get("reminders", {})

                        for rem_id, rem_data in reminders_data.items():
                            reminder = Reminder(
                                id=rem_id,
                                user_id=rem_data["user_id"],
                                guild_id=guild_id,
                                channel_id=rem_data.get("channel_id"),
                                message=rem_data["message"],
                                remind_at=rem_data["remind_at"],
                                recurring=rem_data.get("recurring"),
                                created_at=rem_data["created_at"]
                            )
                            if reminder.remind_at > time.time():
                                self._reminders[rem_id] = reminder
                                count += 1
                    except Exception as e:
                        logger.error(f"Failed to load reminders from {filename}: {e}")
        logger.info(f"Loaded {count} reminders from guild files.")

    def _save_reminder(self, reminder: Reminder):
        """Save a single reminder to its guild-specific data file."""
        guild_id = reminder.guild_id
        reminders = dm.get_guild_data(guild_id, "reminders", {})

        if reminder.id in self._reminders:
            reminders[reminder.id] = {
                "user_id": reminder.user_id,
                "channel_id": reminder.channel_id,
                "message": reminder.message,
                "remind_at": reminder.remind_at,
                "recurring": reminder.recurring,
                "created_at": reminder.created_at
            }
        else:
            reminders.pop(reminder.id, None)

        dm.update_guild_data(guild_id, "reminders", reminders)

    def start_reminder_loop(self):
        asyncio.create_task(self._reminder_loop())

    async def _reminder_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed:
            try:
                current_time = time.time()
                
                for rem_id, reminder in list(self._reminders.items()):
                    if reminder.remind_at <= current_time:
                        await self._send_reminder(reminder)
                        
                        if reminder.recurring:
                            await self._handle_recurring(reminder)
                        else:
                            del self._reminders[rem_id]
                            self._save_reminder(reminder)
            except Exception as e:
                logger.error(f"Reminder loop error: {e}")
            
            await asyncio.sleep(30)

    async def _send_reminder(self, reminder: Reminder):
        user = self.bot.get_user(reminder.user_id)
        
        if not user:
            return
        
        embed = discord.Embed(
            title="⏰ Reminder",
            description=reminder.message,
            color=discord.Color.blue()
        )
        
        created = datetime.fromtimestamp(reminder.created_at).strftime("%Y-%m-%d %H:%M")
        embed.add_field(name="Set At", value=created, inline=True)
        
        if reminder.guild_id:
            guild = self.bot.get_guild(reminder.guild_id)
            if guild:
                embed.add_field(name="Server", value=guild.name, inline=True)
        
        view = ReminderView(reminder.id)
        
        config = dm.get_guild_data(reminder.guild_id, "reminders_config", {})
        allow_dms = config.get("allow_dms", True)
        
        try:
            if allow_dms:
                await user.send(embed=embed, view=view)
            else:
                channel = self.bot.get_channel(reminder.channel_id)
                if channel:
                    await channel.send(content=user.mention, embed=embed, view=view)
        except:
            fallback_ch = config.get("fallback_channel")
            if fallback_ch:
                channel = self.bot.get_channel(fallback_ch)
                if channel:
                    await channel.send(content=user.mention, embed=embed, view=view)

    async def _handle_recurring(self, reminder: Reminder):
        intervals = {
            "daily": 86400,
            "weekly": 604800,
            "monthly": 2592000
        }
        
        if reminder.recurring in intervals:
            new_remind_at = reminder.remind_at + intervals[reminder.recurring]
            
            new_reminder = Reminder(
                id=f"{reminder.id}_{int(time.time())}",
                user_id=reminder.user_id,
                guild_id=reminder.guild_id,
                channel_id=reminder.channel_id,
                message=reminder.message,
                remind_at=new_remind_at,
                recurring=reminder.recurring,
                created_at=reminder.created_at
            )
            
            self._reminders[new_reminder.id] = new_reminder
            self._save_reminder(new_reminder)

    def snooze_reminder(self, reminder_id: str, seconds: int) -> bool:
        if reminder_id in self._reminders:
            reminder = self._reminders[reminder_id]
            reminder.remind_at = time.time() + seconds
            self._save_reminder(reminder)
            return True
        return False

    def _parse_time(self, time_str: str) -> Optional[float]:
        time_str = time_str.lower().strip()
        
        # Handle natural language
        if "tomorrow" in time_str:
            return time.time() + 86400
        
        if "next monday" in time_str:
            now = datetime.now()
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            next_monday = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
            return next_monday.timestamp()
        
        patterns = [
            (r'(\d+)\s*d(?:ays?)?', 86400),
            (r'(\d+)\s*h(?:ours?)?', 3600),
            (r'(\d+)\s*m(?:in(?:utes?)?)?', 60),
            (r'(\d+)\s*s(?:econds?)?', 1),
        ]
        
        total_seconds = 0
        
        for pattern, multiplier in patterns:
            match = re.search(pattern, time_str)
            if match:
                total_seconds += int(match.group(1)) * multiplier
        
        if total_seconds > 0:
            return time.time() + total_seconds
        
        return None

    def _parse_datetime(self, datetime_str: str) -> Optional[float]:
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%H:%M"
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(datetime_str, fmt)
                
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                    
                    if dt < datetime.now():
                        dt = dt.replace(year=datetime.now().year + 1)
                
                return dt.timestamp()
            except:
                continue
        
        return None

    async def create_reminder(self, user_id: int, guild_id: int, channel_id: Optional[int],
                            message: str, time_input: str, recurring: str = None) -> Reminder:
        remind_at = self._parse_time(time_input)
        
        if not remind_at:
            remind_at = self._parse_datetime(time_input)
        
        if not remind_at:
            raise ValueError("Invalid time format. Use '1d', '2h', '30m', 'tomorrow', or 'YYYY-MM-DD HH:MM'")
        
        reminder_id = f"reminder_{guild_id}_{user_id}_{int(time.time())}"
        
        reminder = Reminder(
            id=reminder_id,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            message=message,
            remind_at=remind_at,
            recurring=recurring,
            created_at=time.time()
        )
        
        self._reminders[reminder_id] = reminder
        self._save_reminder(reminder)
        
        return reminder

    def get_user_reminders(self, user_id: int) -> List[Reminder]:
        return [r for r in self._reminders.values() if r.user_id == user_id]

    def cancel_reminder(self, reminder_id: str) -> bool:
        if reminder_id in self._reminders:
            reminder = self._reminders[reminder_id]
            guild_id = reminder.guild_id
            del self._reminders[reminder_id]
            
            reminders = dm.get_guild_data(guild_id, "reminders", {})
            reminders.pop(reminder_id, None)
            dm.update_guild_data(guild_id, "reminders", reminders)
            return True
        return False

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # Create documentation channel
        try:
            doc_channel = await guild.create_text_channel("reminders-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        doc_embed = discord.Embed(
            title="⏰ Reminder System Guide",
            description="Complete guide to setting reminders!",
            color=discord.Color.blue()
        )
        doc_embed.add_field(name="📖 How It Works", value="Set personal reminders and get DM notifications when it's time. Supports recurring reminders and server countdowns.", inline=False)
        doc_embed.add_field(name="🎮 Available Commands", value="**!remind <time> <message>** - Create a reminder\n**!reminders** - List your active reminders\n**!help reminders** - Show this guide", inline=False)
        doc_embed.add_field(name="💡 Time Formats", value="• `30m` = 30 minutes\n• `2h` = 2 hours\n• `1d` = 1 day\n• `1d2h30m` = 1 day, 2 hours, 30 minutes\n• `daily` / `weekly` = recurring", inline=False)
        doc_embed.add_field(name="💡 Examples", value="• `!remind 1h Call mom`\n• `!remind 2h30m Team meeting`\n• `!remind weekly Check reports`", inline=False)
        doc_embed.set_footer(text="Created by Miro AI")
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** Try `!remind 5m Test reminder`")
        
        help_embed = discord.Embed(title="⏰ Reminder System", description="Set personal reminders and server countdowns.", color=discord.Color.green())
        help_embed.add_field(name="How it works", value="Set reminders with natural time formats. Get DM notifications when it's time. Supports recurring reminders.", inline=False)
        help_embed.add_field(name="!remind", value="Create a reminder. Usage: !remind 1h30m Don't forget to do X", inline=False)
        help_embed.add_field(name="!reminders", value="List your active reminders.", inline=False)
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["remind"] = json.dumps({
            "command_type": "create_reminder"
        })
        custom_cmds["reminders"] = json.dumps({
            "command_type": "list_reminders"
        })
        custom_cmds["help reminders"] = json.dumps({
            "command_type": "help_embed",
            "title": "⏰ Reminder System",
            "description": "Set personal reminders.",
            "fields": [
                {"name": "!remind <time> <message>", "value": "Create a reminder.", "inline": False},
                {"name": "!reminders", "value": "List your reminders.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


# ==================== SCHEDULED REMINDERS ====================

class ScheduledPanelView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "scheduled")

    def get_config(self, guild_id: int = None) -> dict:
        return dm.get_guild_data(guild_id or self.guild_id, "scheduled_config", {"enabled": True})

    def save_config(self, config: dict, guild_id: int = None, client = None):
        dm.update_guild_data(guild_id or self.guild_id, "scheduled_config", config)

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="📅 Scheduled Messages Configuration", color=discord.Color.purple())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)

        scheduled = dm.get_guild_data(guild_id or self.guild_id, "scheduled_reminders", {})
        embed.add_field(name="Total Scheduled", value=str(len(scheduled)), inline=True)

        if scheduled:
            active = sum(1 for s in scheduled.values() if s.get("enabled", True))
            embed.add_field(name="Active Tasks", value=str(active), inline=True)

        return embed

    @ui.button(label="View All Scheduled", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_sched_viewall")
    async def view_all(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled reminders.", ephemeral=True)
        
        desc = ""
        for sid, s in list(scheduled.items())[:10]:
            status = "✅" if s.get("enabled", True) else "⏸️"
            next_send = f"<t:{int(s['send_at'])}:R>" if s.get("send_at") else "N/A"
            desc += f"{status} **{s['name']}** → {s['channel']} | Next: {next_send}\n"
        
        embed = discord.Embed(title="📋 Scheduled Reminders", description=desc, color=discord.Color.blue())
        embed.set_footer(text=f"Total: {len(scheduled)} | Showing first 10")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Create Scheduled", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_sched_create")
    async def create(self, interaction: discord.Interaction, button: ui.Button):
        modal = CreateScheduledModal(self)
        await interaction.response.send_modal(modal)

    @ui.button(label="Edit Scheduled", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_sched_edit")
    async def edit(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled reminders to edit.", ephemeral=True)
        
        class SchedSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["name"], value=k) for k, s in list(scheduled.items())[:25]]
                super().__init__(placeholder="Select reminder to edit...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                modal = EditScheduledModal(self.panel, self.values[0])
                await inner_interaction.response.send_modal(modal)
        
        view = ui.View()
        view.add_item(SchedSelect(self))
        await interaction.response.send_message("Select a reminder to edit:", view=view, ephemeral=True)

    @ui.button(label="Pause Reminder", emoji="⏸️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_sched_pause")
    async def pause(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        enabled = {k: v for k, v in scheduled.items() if v.get("enabled", True)}
        
        if not enabled:
            return await interaction.response.send_message("📭 No active reminders to pause.", ephemeral=True)
        
        class PauseSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["name"], value=k) for k, s in list(enabled.items())[:25]]
                super().__init__(placeholder="Select reminder to pause...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                scheduled[self.values[0]]["enabled"] = False
                dm.update_guild_data(inner_interaction.guild_id, "scheduled_reminders", scheduled)
                await inner_interaction.response.send_message(f"⏸️ Paused: {scheduled[self.values[0]]['name']}", ephemeral=True)
        
        view = ui.View()
        view.add_item(PauseSelect(self))
        await interaction.response.send_message("Select a reminder to pause:", view=view, ephemeral=True)

    @ui.button(label="Resume Reminder", emoji="▶️", style=discord.ButtonStyle.success, row=1, custom_id="cfg_sched_resume")
    async def resume(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        paused = {k: v for k, v in scheduled.items() if not v.get("enabled", True)}
        
        if not paused:
            return await interaction.response.send_message("📭 No paused reminders.", ephemeral=True)
        
        class ResumeSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["name"], value=k) for k, s in list(paused.items())[:25]]
                super().__init__(placeholder="Select reminder to resume...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                scheduled[self.values[0]]["enabled"] = True
                dm.update_guild_data(inner_interaction.guild_id, "scheduled_reminders", scheduled)
                await inner_interaction.response.send_message(f"▶️ Resumed: {scheduled[self.values[0]]['name']}", ephemeral=True)
        
        view = ui.View()
        view.add_item(ResumeSelect(self))
        await interaction.response.send_message("Select a reminder to resume:", view=view, ephemeral=True)

    @ui.button(label="Delete Reminder", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_sched_delete")
    async def delete(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled reminders.", ephemeral=True)
        
        class DeleteSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["name"], value=k) for k, s in list(scheduled.items())[:25]]
                super().__init__(placeholder="Select reminder to delete...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                modal = ConfirmDeleteModal(self.panel, self.values[0])
                await inner_interaction.response.send_modal(modal)
        
        view = ui.View()
        view.add_item(DeleteSelect(self))
        await interaction.response.send_message("Select a reminder to delete:", view=view, ephemeral=True)

    @ui.button(label="Send Now", emoji="▶️", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_sched_sendnow")
    async def send_now(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled reminders.", ephemeral=True)
        
        class SendNowSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["name"], value=k) for k, s in list(scheduled.items())[:25]]
                super().__init__(placeholder="Select reminder to send now...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                s = scheduled[self.values[0]]
                channel = inner_interaction.guild.get_channel(int(s["channel_id"]))
                if channel:
                    embed = discord.Embed(title=s["name"], description=s["message"], color=discord.Color.blue())
                    if s.get("role_id"):
                        role = inner_interaction.guild.get_role(int(s["role_id"]))
                        content = role.mention if role else ""
                    else:
                        content = ""
                    await channel.send(content=content, embed=embed)
                    await inner_interaction.response.send_message(f"✅ Sent: {s['name']}", ephemeral=True)
                else:
                    await inner_interaction.response.send_message("❌ Channel not found.", ephemeral=True)
        
        view = ui.View()
        view.add_item(SendNowSelect(self))
        await interaction.response.send_message("Select a reminder to send now:", view=view, ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_sched_stats")
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
        now = time.time()
        week_start = now - 604800
        
        total = len(scheduled)
        enabled = sum(1 for s in scheduled.values() if s.get("enabled", True))
        sent_week = sum(1 for s in scheduled.values() if s.get("last_sent", 0) > week_start)
        
        next_upcoming = None
        earliest = float('inf')
        for s in scheduled.values():
            if s.get("enabled", True) and s.get("send_at", float('inf')) < earliest:
                earliest = s["send_at"]
                next_upcoming = s
        
        embed = discord.Embed(title="📊 Scheduled Reminders Stats", color=discord.Color.green())
        embed.add_field(name="Total", value=str(total), inline=True)
        embed.add_field(name="Enabled", value=str(enabled), inline=True)
        embed.add_field(name="Sent This Week", value=str(sent_week), inline=True)
        if next_upcoming:
            embed.add_field(name="Next Upcoming", value=f"{next_upcoming['name']} (<t:{int(next_upcoming['send_at'])}:R>)", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Cron Helper", emoji="🔄", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_sched_cronhelp")
    async def cron_help(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="🔄 Cron Format Helper", description="Cron expressions: `minute hour day month weekday`", color=discord.Color.blue())
        embed.add_field(name="Examples", value="`* * * * *` - Every minute\n`0 * * * *` - Every hour\n`0 0 * * *` - Daily at midnight\n`0 9 * * 1` - Every Monday at 9am\n`0 0 1 * *` - First of every month", inline=False)
        embed.add_field(name="Special Values", value="`@hourly` - Every hour\n`@daily` - Every day at midnight\n`@weekly` - Every Sunday at midnight", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CreateScheduledModal(ui.Modal, title="Create Scheduled Reminder"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.name_input = ui.TextInput(label="Name", required=True, max_length=50)
        self.message_input = ui.TextInput(label="Message", style=discord.TextStyle.paragraph, required=True, max_length=500)
        self.cron_input = ui.TextInput(label="Cron Expression (e.g., 0 9 * * *)", required=True, max_length=50)
        self.channel_input = ui.TextInput(label="Channel ID", required=True, max_length=30)
        self.role_input = ui.TextInput(label="Role ID to ping (optional)", required=False, max_length=30)
        self.add_item(self.name_input)
        self.add_item(self.message_input)
        self.add_item(self.cron_input)
        self.add_item(self.channel_input)
        self.add_item(self.role_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse cron to next occurrence (simplified)
            next_run = parse_cron_to_next(self.cron_input.value)
            
            scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
            sid = f"sched_{interaction.guild_id}_{int(time.time())}"
            
            scheduled[sid] = {
                "id": sid,
                "name": self.name_input.value,
                "message": self.message_input.value,
                "cron": self.cron_input.value,
                "channel_id": self.channel_input.value,
                "channel": f"<#{self.channel_input.value}>",
                "role_id": self.role_input.value if self.role_input.value else None,
                "send_at": next_run,
                "enabled": True,
                "created_at": time.time()
            }
            
            dm.update_guild_data(interaction.guild_id, "scheduled_reminders", scheduled)
            asyncio.create_task(schedule_checker(interaction.client, interaction.guild_id, sid))
            
            await interaction.response.send_message(f"✅ Scheduled: {self.name_input.value} (next: <t:{int(next_run)}:R>)", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


class EditScheduledModal(ui.Modal, title="Edit Scheduled Reminder"):
    def __init__(self, panel, scheduled_id: str):
        super().__init__()
        self.panel = panel
        self.scheduled_id = scheduled_id
        scheduled = dm.get_guild_data(0, "scheduled_reminders", {}).get(scheduled_id, {})
        
        self.name_input = ui.TextInput(label="Name", required=True, max_length=50, default=scheduled.get("name", ""))
        self.message_input = ui.TextInput(label="Message", style=discord.TextStyle.paragraph, required=True, max_length=500, default=scheduled.get("message", ""))
        self.cron_input = ui.TextInput(label="Cron Expression", required=True, max_length=50, default=scheduled.get("cron", ""))
        self.add_item(self.name_input)
        self.add_item(self.message_input)
        self.add_item(self.cron_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
            if self.scheduled_id in scheduled:
                scheduled[self.scheduled_id]["name"] = self.name_input.value
                scheduled[self.scheduled_id]["message"] = self.message_input.value
                scheduled[self.scheduled_id]["cron"] = self.cron_input.value
                scheduled[self.scheduled_id]["send_at"] = parse_cron_to_next(self.cron_input.value)
                dm.update_guild_data(interaction.guild_id, "scheduled_reminders", scheduled)
                await interaction.response.send_message("✅ Reminder updated.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


class ConfirmDeleteModal(ui.Modal, title="Confirm Delete"):
    def __init__(self, panel, scheduled_id: str):
        super().__init__()
        self.panel = panel
        self.scheduled_id = scheduled_id
        self.confirm_input = ui.TextInput(label=f"Type DELETE to confirm", required=True, max_length=20)
        self.add_item(self.confirm_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm_input.value.upper() == "DELETE":
            scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_reminders", {})
            if self.scheduled_id in scheduled:
                name = scheduled[self.scheduled_id]["name"]
                del scheduled[self.scheduled_id]
                dm.update_guild_data(interaction.guild_id, "scheduled_reminders", scheduled)
                await interaction.response.send_message(f"🗑️ Deleted: {name}", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Confirmation failed. Type DELETE exactly.", ephemeral=True)


def parse_cron_to_next(cron_expr: str) -> float:
    """Simplified cron parser - returns next occurrence."""
    cron_expr = cron_expr.strip()
    
    # Handle special expressions
    if cron_expr == "@hourly":
        return time.time() + 3600
    elif cron_expr == "@daily":
        return time.time() + 86400
    elif cron_expr == "@weekly":
        return time.time() + 604800
    
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError("Invalid cron expression. Expected 5 parts.")
    
    # Simplified: just add 1 hour for demo
    return time.time() + 3600


async def schedule_checker(bot, guild_id: str, scheduled_id: str):
    """Background task to check and send scheduled reminders."""
    while True:
        await asyncio.sleep(60)
        scheduled = dm.get_guild_data(guild_id, "scheduled_reminders", {})
        if scheduled_id not in scheduled:
            break
        
        s = scheduled[scheduled_id]
        if not s.get("enabled", True):
            continue
        
        if time.time() >= s.get("send_at", float('inf')):
            channel = bot.get_guild(guild_id).get_channel(int(s["channel_id"]))
            if channel:
                embed = discord.Embed(title=s["name"], description=s["message"], color=discord.Color.blue())
                content = ""
                if s.get("role_id"):
                    role = bot.get_guild(guild_id).get_role(int(s["role_id"]))
                    if role:
                        content = role.mention
                
                await channel.send(content=content, embed=embed)
                scheduled[scheduled_id]["last_sent"] = time.time()
                scheduled[scheduled_id]["send_at"] = parse_cron_to_next(s["cron"])
                dm.update_guild_data(guild_id, "scheduled_reminders", scheduled)


# ==================== ANNOUNCEMENTS SYSTEM ====================

class AnnouncementsPanelView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "announcements")

    def get_config(self, guild_id: int = None) -> dict:
        return dm.get_guild_data(guild_id or self.guild_id, "announcements_config", {
            "enabled": True,
            "channel_id": None,
            "ping_role_id": None,
            "auto_pin": True,
            "cross_post": False,
            "require_approval": False,
            "approval_channel_id": None
        })

    def save_config(self, config: dict, guild_id: int = None, client = None):
        dm.update_guild_data(guild_id or self.guild_id, "announcements_config", config)

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="📢 Announcements System Configuration", color=discord.Color.gold())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Channel", value=f"<#{c.get('channel_id')}>" if c.get('channel_id') else "_Not Set_", inline=True)
        embed.add_field(name="Ping Role", value=f"<@&{c.get('ping_role_id')}>" if c.get('ping_role_id') else "_None_", inline=True)
        embed.add_field(name="Auto-Pin", value="ON" if c.get("auto_pin", True) else "OFF", inline=True)
        embed.add_field(name="Cross-Post", value="ON" if c.get("cross_post", False) else "OFF", inline=True)
        embed.add_field(name="Approval Req.", value="YES" if c.get("require_approval", False) else "NO", inline=True)

        if c.get("require_approval"):
            embed.add_field(name="Approval Ch", value=f"<#{c.get('approval_channel_id')}>" if c.get('approval_channel_id') else "_Not Set_", inline=True)

        logs = dm.get_guild_data(guild_id or self.guild_id, "announcements_log", [])
        embed.add_field(name="Total Posted", value=str(len(logs)), inline=True)
        return embed

    @ui.button(label="New Announcement", emoji="📢", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_announce_new")
    async def new_announcement(self, interaction: discord.Interaction, button: ui.Button):
        class TypeSelect(ui.Select):
            def __init__(self, panel):
                options = [
                    discord.SelectOption(label="Standard", value="standard", emoji="📝"),
                    discord.SelectOption(label="Update", value="update", emoji="🔄"),
                    discord.SelectOption(label="Event", value="event", emoji="🎉"),
                    discord.SelectOption(label="Poll", value="poll", emoji="📊"),
                    discord.SelectOption(label="Emergency", value="emergency", emoji="🚨"),
                    discord.SelectOption(label="Scheduled", value="scheduled", emoji="⏰"),
                ]
                super().__init__(placeholder="Select announcement type...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                modal_type = self.values[0]
                if modal_type == "standard":
                    modal = StandardAnnounceModal(self.panel)
                elif modal_type == "update":
                    modal = UpdateAnnounceModal(self.panel)
                elif modal_type == "event":
                    modal = EventAnnounceModal(self.panel)
                elif modal_type == "poll":
                    modal = PollAnnounceModal(self.panel)
                elif modal_type == "emergency":
                    modal = EmergencyAnnounceModal(self.panel)
                elif modal_type == "scheduled":
                    modal = ScheduledAnnounceModal(self.panel)
                else:
                    return
                await inner_interaction.response.send_modal(modal)
        
        view = ui.View()
        view.add_item(TypeSelect(self))
        await interaction.response.send_message("Select announcement type:", view=view, ephemeral=True)

    @ui.button(label="View Scheduled", emoji="📋", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_announce_viewsched")
    async def view_scheduled(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_announcements", {})
        
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled announcements.", ephemeral=True)
        
        desc = ""
        for sid, s in list(scheduled.items())[:10]:
            desc += f"**{s['title']}** → <t:{int(s['post_at'])}:R>\n{s['content'][:50]}...\n"
        
        embed = discord.Embed(title="📋 Scheduled Announcements", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Edit Scheduled", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_announce_editsched")
    async def edit_scheduled(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_announcements", {})
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled announcements.", ephemeral=True)
        
        class SchedSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["title"], value=k) for k, s in list(scheduled.items())[:25]]
                super().__init__(placeholder="Select to edit...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                modal = EditScheduledAnnounceModal(self.panel, self.values[0])
                await inner_interaction.response.send_modal(modal)
        
        view = ui.View()
        view.add_item(SchedSelect(self))
        await interaction.response.send_message("Select announcement to edit:", view=view, ephemeral=True)

    @ui.button(label="Cancel Scheduled", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_announce_cancelsched")
    async def cancel_scheduled(self, interaction: discord.Interaction, button: ui.Button):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_announcements", {})
        if not scheduled:
            return await interaction.response.send_message("📭 No scheduled announcements.", ephemeral=True)
        
        class CancelSelect(ui.Select):
            def __init__(self, panel):
                options = [discord.SelectOption(label=s["title"], value=k) for k, s in list(scheduled.items())[:25]]
                super().__init__(placeholder="Select to cancel...", options=options)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                del scheduled[self.values[0]]
                dm.update_guild_data(inner_interaction.guild_id, "scheduled_announcements", scheduled)
                await inner_interaction.response.send_message("✅ Announcement cancelled.", ephemeral=True)
        
        view = ui.View()
        view.add_item(CancelSelect(self))
        await interaction.response.send_message("Select announcement to cancel:", view=view, ephemeral=True)

    @ui.button(label="Set Channel", emoji="📣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_announce_setchan")
    async def set_channel(self, interaction: discord.Interaction, button: ui.Button):
        class ChannelSelect(ui.ChannelSelect):
            def __init__(self, panel):
                super().__init__(placeholder="Select announcements channel...", min_values=1, max_values=1)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                c = self.panel.get_config(inner_interaction.guild_id)
                c["channel_id"] = self.values[0].id
                self.panel.save_config(c, inner_interaction.guild_id, inner_interaction.client)
                await inner_interaction.response.send_message(f"✅ Announcements channel set to {self.values[0].mention}.", ephemeral=True)
                await self.panel.update_panel(inner_interaction)
        
        view = ui.View()
        view.add_item(ChannelSelect(self))
        await interaction.response.send_message("Select announcements channel:", view=view, ephemeral=True)

    @ui.button(label="Set Ping Role", emoji="🔔", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_announce_setrole")
    async def set_ping_role(self, interaction: discord.Interaction, button: ui.Button):
        class RoleSelect(ui.RoleSelect):
            def __init__(self, panel):
                super().__init__(placeholder="Select default ping role...", min_values=1, max_values=1)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                c = self.panel.get_config(inner_interaction.guild_id)
                c["ping_role_id"] = self.values[0].id
                self.panel.save_config(c, inner_interaction.guild_id, inner_interaction.client)
                await inner_interaction.response.send_message(f"✅ Default ping role set to {self.values[0].mention}.", ephemeral=True)
                await self.panel.update_panel(inner_interaction)
        
        view = ui.View()
        view.add_item(RoleSelect(self))
        await interaction.response.send_message("Select default ping role:", view=view, ephemeral=True)

    @ui.button(label="Toggle Auto-Pin", emoji="📌", style=discord.ButtonStyle.success, row=2, custom_id="cfg_announce_autopin")
    async def toggle_autopin(self, interaction: discord.Interaction, button: ui.Button):
        c = self.get_config(interaction.guild_id)
        c["auto_pin"] = not c.get("auto_pin", True)
        self.save_config(c, interaction.guild_id, interaction.client)
        status = "✅" if c["auto_pin"] else "❌"
        await interaction.response.send_message(f"{status} Auto-pin {'enabled' if c['auto_pin'] else 'disabled'}.", ephemeral=True)
        await self.update_panel(interaction)

    @ui.button(label="Toggle Cross-Post", emoji="🌐", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_announce_crosspost")
    async def toggle_crosspost(self, interaction: discord.Interaction, button: ui.Button):
        c = self.get_config(interaction.guild_id)
        c["cross_post"] = not c.get("cross_post", False)
        self.save_config(c, interaction.guild_id, interaction.client)
        status = "✅" if c["cross_post"] else "❌"
        await interaction.response.send_message(f"{status} Cross-posting {'enabled' if c['cross_post'] else 'disabled'}.", ephemeral=True)
        await self.update_panel(interaction)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_announce_stats")
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        announcements = dm.get_guild_data(interaction.guild_id, "announcements_log", [])
        now = time.time()
        month_start = now - 2592000
        
        total_month = sum(1 for a in announcements if a.get("created_at", 0) > month_start)
        
        # Most engaged
        most_engaged = None
        max_reactions = 0
        for a in announcements:
            reactions = a.get("reactions", 0)
            if reactions > max_reactions:
                max_reactions = reactions
                most_engaged = a
        
        embed = discord.Embed(title="📊 Announcements Stats", color=discord.Color.green())
        embed.add_field(name="This Month", value=str(total_month), inline=True)
        if most_engaged:
            embed.add_field(name="Most Engaged", value=f"{most_engaged['title']} ({max_reactions} reactions)", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="View History", emoji="📋", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_announce_history")
    async def view_history(self, interaction: discord.Interaction, button: ui.Button):
        announcements = dm.get_guild_data(interaction.guild_id, "announcements_log", [])
        
        if not announcements:
            return await interaction.response.send_message("📭 No announcement history.", ephemeral=True)
        
        desc = ""
        for a in list(announcements)[-20:][::-1]:
            desc += f"**{a['title']}** - <t:{int(a['created_at'])}:R> ({a.get('reactions', 0)} reactions)\n"
        
        embed = discord.Embed(title="📋 Announcement History", description=desc, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Toggle Approval", emoji="✅", style=discord.ButtonStyle.success, row=3, custom_id="cfg_announce_toggleapprove")
    async def toggle_approval(self, interaction: discord.Interaction, button: ui.Button):
        c = self.get_config(interaction.guild_id)
        c["require_approval"] = not c.get("require_approval", False)
        self.save_config(c, interaction.guild_id, interaction.client)
        status = "✅" if c["require_approval"] else "❌"
        await interaction.response.send_message(f"{status} Approval requirement {'enabled' if c['require_approval'] else 'disabled'}.", ephemeral=True)
        await self.update_panel(interaction)

    @ui.button(label="Set Approval Channel", emoji="📣", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_announce_setapprove")
    async def set_approval_channel(self, interaction: discord.Interaction, button: ui.Button):
        class ChannelSelect(ui.ChannelSelect):
            def __init__(self, panel):
                super().__init__(placeholder="Select approval channel...", min_values=1, max_values=1)
                self.panel = panel
            
            async def callback(self, inner_interaction: discord.Interaction):
                c = self.panel.get_config(inner_interaction.guild_id)
                c["approval_channel_id"] = self.values[0].id
                self.panel.save_config(c, inner_interaction.guild_id, inner_interaction.client)
                await inner_interaction.response.send_message(f"✅ Approval channel set to {self.values[0].mention}.", ephemeral=True)
                await self.panel.update_panel(inner_interaction)
        
        view = ui.View()
        view.add_item(ChannelSelect(self))
        await interaction.response.send_message("Select approval channel:", view=view, ephemeral=True)


class StandardAnnounceModal(ui.Modal, title="Standard Announcement"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.title_input = ui.TextInput(label="Title", required=True, max_length=100)
        self.content_input = ui.TextInput(label="Content", style=discord.TextStyle.paragraph, required=True, max_length=2000)
        self.image_input = ui.TextInput(label="Image URL (optional)", required=False, max_length=500)
        self.add_item(self.title_input)
        self.add_item(self.content_input)
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction):
        await send_announcement(interaction, "standard", {
            "title": self.title_input.value,
            "content": self.content_input.value,
            "image": self.image_input.value if self.image_input.value else None
        })


class UpdateAnnounceModal(ui.Modal, title="Update Announcement"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.version_input = ui.TextInput(label="Version Number", required=True, max_length=50)
        self.whats_new = ui.TextInput(label="What's New", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.whats_fixed = ui.TextInput(label="What's Fixed", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.version_input)
        self.add_item(self.whats_new)
        self.add_item(self.whats_fixed)

    async def on_submit(self, interaction: discord.Interaction):
        await send_announcement(interaction, "update", {
            "version": self.version_input.value,
            "whats_new": self.whats_new.value,
            "whats_fixed": self.whats_fixed.value
        })


class EventAnnounceModal(ui.Modal, title="Event Announcement"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.event_name = ui.TextInput(label="Event Name", required=True, max_length=100)
        self.date_time = ui.TextInput(label="Date/Time (e.g., 2024-12-25 18:00)", required=True, max_length=50)
        self.location = ui.TextInput(label="Location/Voice Channel", required=True, max_length=200)
        self.description = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=1000)
        self.add_item(self.event_name)
        self.add_item(self.date_time)
        self.add_item(self.location)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        await send_announcement(interaction, "event", {
            "event_name": self.event_name.value,
            "date_time": self.date_time.value,
            "location": self.location.value,
            "description": self.description.value
        })


class PollAnnounceModal(ui.Modal, title="Poll Announcement"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.question = ui.TextInput(label="Question", required=True, max_length=200)
        self.options = ui.TextInput(label="Options (comma-separated, max 10)", style=discord.TextStyle.paragraph, required=True, max_length=500)
        self.duration = ui.TextInput(label="Duration (hours)", required=True, max_length=10)
        self.add_item(self.question)
        self.add_item(self.options)
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        await send_announcement(interaction, "poll", {
            "question": self.question.value,
            "options": self.options.value.split(","),
            "duration_hours": int(self.duration.value)
        })


class EmergencyAnnounceModal(ui.Modal, title="Emergency Announcement"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.title_input = ui.TextInput(label="Title", required=True, max_length=100)
        self.content_input = ui.TextInput(label="URGENT Message", style=discord.TextStyle.paragraph, required=True, max_length=2000)
        self.add_item(self.title_input)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        await send_announcement(interaction, "emergency", {
            "title": self.title_input.value,
            "content": self.content_input.value
        })


class ScheduledAnnounceModal(ui.Modal, title="Scheduled Announcement"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.title_input = ui.TextInput(label="Title", required=True, max_length=100)
        self.content_input = ui.TextInput(label="Content", style=discord.TextStyle.paragraph, required=True, max_length=2000)
        self.post_at = ui.TextInput(label="Post at (YYYY-MM-DD HH:MM)", required=True, max_length=50)
        self.add_item(self.title_input)
        self.add_item(self.content_input)
        self.add_item(self.post_at)

    async def on_submit(self, interaction: discord.Interaction):
        await send_announcement(interaction, "scheduled", {
            "title": self.title_input.value,
            "content": self.content_input.value,
            "post_at": self.post_at.value
        })


class EditScheduledAnnounceModal(ui.Modal, title="Edit Scheduled Announcement"):
    def __init__(self, panel, announce_id: str):
        super().__init__()
        self.panel = panel
        self.announce_id = announce_id
        scheduled = dm.get_guild_data(0, "scheduled_announcements", {}).get(announce_id, {})
        
        self.title_input = ui.TextInput(label="Title", required=True, max_length=100, default=scheduled.get("title", ""))
        self.content_input = ui.TextInput(label="Content", style=discord.TextStyle.paragraph, required=True, max_length=2000, default=scheduled.get("content", ""))
        self.add_item(self.title_input)
        self.add_item(self.content_input)

    async def on_submit(self, interaction: discord.Interaction):
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_announcements", {})
        if self.announce_id in scheduled:
            scheduled[self.announce_id]["title"] = self.title_input.value
            scheduled[self.announce_id]["content"] = self.content_input.value
            dm.update_guild_data(interaction.guild_id, "scheduled_announcements", scheduled)
            await interaction.response.send_message("✅ Announcement updated.", ephemeral=True)


async def send_announcement(interaction: discord.Interaction, announce_type: str, data: dict):
    config = dm.get_guild_data(interaction.guild_id, "announcements_config", {})
    channel_id = config.get("channel_id")
    
    if not channel_id:
        return await interaction.response.send_message("❌ No announcements channel configured. Use the panel to set one.", ephemeral=True)
    
    channel = interaction.guild.get_channel(channel_id)
    if not channel:
        return await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
    
    # Build embed based on type
    embed = discord.Embed(color=discord.Color.blue())
    content = ""
    
    if announce_type == "standard":
        embed.title = f"📢 {data['title']}"
        embed.description = data['content']
        if data.get('image'):
            embed.set_image(url=data['image'])
    
    elif announce_type == "update":
        embed.title = f"🔄 Update v{data['version']}"
        embed.add_field(name="✨ What's New", value=data['whats_new'], inline=False)
        embed.add_field(name="🐛 What's Fixed", value=data['whats_fixed'], inline=False)
        embed.color = discord.Color.green()
    
    elif announce_type == "event":
        embed.title = f"🎉 {data['event_name']}"
        embed.description = data['description']
        embed.add_field(name="📅 When", value=data['date_time'], inline=True)
        embed.add_field(name="📍 Where", value=data['location'], inline=True)
        embed.color = discord.Color.gold()
    
    elif announce_type == "poll":
        embed.title = f"📊 {data['question']}"
        options = data['options'][:10]
        for i, opt in enumerate(options):
            embed.add_field(name=f"Option {i+1}", value=opt.strip(), inline=False)
        embed.color = discord.Color.purple()
    
    elif announce_type == "emergency":
        embed.title = f"🚨 URGENT: {data['title']}"
        embed.description = data['content']
        embed.color = discord.Color.red()
        content = "@everyone"
    
    elif announce_type == "scheduled":
        # Parse post_at and schedule
        try:
            post_dt = datetime.strptime(data['post_at'], "%Y-%m-%d %H:%M")
            post_ts = post_dt.timestamp()
        except:
            return await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD HH:MM", ephemeral=True)
        
        scheduled = dm.get_guild_data(interaction.guild_id, "scheduled_announcements", {})
        sid = f"announce_{interaction.guild_id}_{int(time.time())}"
        scheduled[sid] = {
            "id": sid,
            "title": data['title'],
            "content": data['content'],
            "post_at": post_ts,
            "created_at": time.time()
        }
        dm.update_guild_data(interaction.guild_id, "scheduled_announcements", scheduled)
        
        return await interaction.response.send_message(f"✅ Announcement scheduled for <t:{int(post_ts)}:R>", ephemeral=True)
    
    # Add role ping if configured
    if config.get("ping_role_id"):
        role = interaction.guild.get_role(config["ping_role_id"])
        if role:
            content += f" {role.mention}"
    
    msg = await channel.send(content=content.strip(), embed=embed)
    
    # Auto-pin if enabled
    if config.get("auto_pin", True):
        await msg.pin()
    
    # Cross-post if enabled and news channel
    if config.get("cross_post", False) and isinstance(channel, discord.TextChannel) and channel.is_news():
        try:
            await msg.publish()
        except:
            pass
    
    # Log announcement
    log = dm.get_guild_data(interaction.guild_id, "announcements_log", [])
    log.append({
        "title": embed.title,
        "type": announce_type,
        "created_at": time.time(),
        "author_id": interaction.user.id,
        "message_id": msg.id,
        "reactions": 0
    })
    dm.update_guild_data(interaction.guild_id, "announcements_log", log)
    
    await interaction.response.send_message(f"✅ Announcement posted to {channel.mention}!", ephemeral=True)


# Register persistent views
def register_announcement_views(bot: discord.Client):
    bot.add_view(RemindersPanelView(0))
    bot.add_view(ScheduledPanelView(0))
    bot.add_view(AnnouncementsPanelView(0))
    logger.info("Part 5 announcement views registered.")
