import discord
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


class ReminderSystem:
    def __init__(self, bot):
        self.bot = bot
        self._reminders: Dict[str, Reminder] = {}
        self._load_reminders()
        self._start_reminder_loop()

    def _load_reminders(self):
        data = dm.load_json("reminders", default={})
        
        for rem_id, rem_data in data.items():
            try:
                reminder = Reminder(
                    id=rem_id,
                    user_id=rem_data["user_id"],
                    guild_id=rem_data["guild_id"],
                    channel_id=rem_data.get("channel_id"),
                    message=rem_data["message"],
                    remind_at=rem_data["remind_at"],
                    recurring=rem_data.get("recurring"),
                    created_at=rem_data["created_at"]
                )
                
                if reminder.remind_at > time.time():
                    self._reminders[rem_id] = reminder
            except Exception as e:
                logger.error(f"Failed to load reminder {rem_id}: {e}")

    def _save_reminder(self, reminder: Reminder):
        data = dm.load_json("reminders", default={})
        data[reminder.id] = {
            "user_id": reminder.user_id,
            "guild_id": reminder.guild_id,
            "channel_id": reminder.channel_id,
            "message": reminder.message,
            "remind_at": reminder.remind_at,
            "recurring": reminder.recurring,
            "created_at": reminder.created_at
        }
        dm.save_json("reminders", data)

    def _start_reminder_loop(self):
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
        
        if reminder.guild_id:
            guild = self.bot.get_guild(reminder.guild_id)
            if guild:
                embed.add_field(name="Server", value=guild.name, inline=True)
        
        try:
            await user.send(embed=embed)
        except:
            pass

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

    def _parse_time(self, time_str: str) -> Optional[float]:
        time_str = time_str.lower().strip()
        
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
            raise ValueError("Invalid time format. Use '1d', '2h', '30m', or 'YYYY-MM-DD HH:MM'")
        
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
            del self._reminders[reminder_id]
            
            data = dm.load_json("reminders", default={})
            if reminder_id in data:
                del data[reminder_id]
                dm.save_json("reminders", data)
            
            return True
        return False

    async def create_countdown(self, guild_id: int, channel_id: int, event_name: str, 
                              end_time: float, message: str = None):
        countdown_id = f"countdown_{guild_id}_{int(time.time())}"
        
        countdown = {
            "id": countdown_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "event_name": event_name,
            "end_time": end_time,
            "message": message or f"Event: {event_name}"
        }
        
        countdowns = dm.get_guild_data(guild_id, "countdowns", {})
        countdowns[countdown_id] = countdown
        dm.update_guild_data(guild_id, "countdowns", countdowns)
        
        asyncio.create_task(self._run_countdown(countdown))
        
        return countdown

    async def _run_countdown(self, countdown: dict):
        channel = self.bot.get_channel(countdown["channel_id"])
        if not channel:
            return
        
        while time.time() < countdown["end_time"]:
            remaining = countdown["end_time"] - time.time()
            
            if remaining <= 0:
                break
            
            if remaining <= 60:
                time_str = f"{int(remaining)}s"
            elif remaining <= 3600:
                time_str = f"{int(remaining / 60)}m"
            elif remaining <= 86400:
                time_str = f"{int(remaining / 3600)}h"
            else:
                time_str = f"{int(remaining / 86400)}d"
            
            embed = discord.Embed(
                title=f"⏳ {countdown['event_name']}",
                description=f"Time remaining: **{time_str}**",
                color=discord.Color.gold()
            )
            
            try:
                await channel.send(embed=embed)
            except:
                pass
            
            await asyncio.sleep(min(remaining, 3600))
        
        embed = discord.Embed(
            title=f"🎉 {countdown['event_name']}",
            description=countdown.get("message", "The event has started!"),
            color=discord.Color.green()
        )
        
        try:
            await channel.send(embed=embed)
        except:
            pass

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        help_embed = discord.Embed(
            title="⏰ Reminder System",
            description="Set personal reminders and server countdowns.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Set reminders with natural time formats. Get DM notifications when it's time. Supports recurring reminders.",
            inline=False
        )
        help_embed.add_field(
            name="!remind",
            value="Create a reminder. Usage: !remind 1h30m Don't forget to do X",
            inline=False
        )
        help_embed.add_field(
            name="!reminders",
            value="List your active reminders.",
            inline=False
        )
        
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


from discord import app_commands
