import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger


class StaffShiftSystem:
    def __init__(self, bot):
        self.bot = bot
        self._shifts: Dict[int, Dict[int, dict]] = {}
        self._tasks: Dict[int, Dict[int, dict]] = {}
        self._warnings: Dict[int, Dict[int, dict]] = {}
        self._activity_logs: Dict[int, List[dict]] = {}
        self._load_data()

    def _load_data(self):
        data = dm.load_json("staff_shifts_tasks", default={})
        self._shifts = data.get("shifts", {})
        self._tasks = data.get("tasks", {})
        self._warnings = data.get("warnings", {})
        self._activity_logs = data.get("activity_logs", {})

    def _save_data(self):
        data = {
            "shifts": self._shifts,
            "tasks": self._tasks,
            "warnings": self._warnings,
            "activity_logs": self._activity_logs
        }
        dm.save_json("staff_shifts_tasks", data)

    async def handle_shift_start(self, message, parts):
        """Handle !shift start command"""
        guild = message.guild
        guild_id = guild.id
        user_id = message.author.id
        
        if guild_id not in self._shifts:
            self._shifts[guild_id] = {}
        
        self._shifts[guild_id][user_id] = {
            "started_at": time.time(),
            "started_by": str(message.author),
            "active": True
        }
        
        await self.log_action(guild_id, user_id, "shift_start", "")
        
        await message.channel.send(f"✅ Shift started!")

    async def handle_shift_end(self, message):
        """Handle !shift end command"""
        guild = message.guild
        guild_id = guild.id
        user_id = message.author.id
        
        if guild_id in self._shifts and user_id in self._shifts[guild_id]:
            shift_data = self._shifts[guild_id][user_id]
            start_time = shift_data.get("started_at", time.time())
            duration = time.time() - start_time
            hours = duration / 3600
            
            await self.log_action(guild_id, user_id, "shift_end", f"{hours:.1f} hours")
            
            del self._shifts[guild_id][user_id]
            
            await message.channel.send(f"✅ Shift ended! Duration: {hours:.1f} hours")
        else:
            await message.channel.send("You don't have an active shift!")

    async def handle_show_shifts(self, message):
        """Handle !show shifts command"""
        guild = message.guild
        guild_id = guild.id
        
        shifts = self._shifts.get(guild_id, {})
        
        if not shifts:
            await message.channel.send("No active shifts!")
            return
        
        embed = discord.Embed(
            title="📅 Active Shifts",
            color=discord.Color.blue()
        )
        
        for user_id, shift_data in shifts.items():
            member = guild.get_member(user_id)
            name = member.display_name if member else shift_data.get("started_by", "Unknown")
            started = datetime.fromtimestamp(shift_data.get("started_at", 0)).strftime("%H:%M")
            embed.add_field(
                name=name,
                value=f"Started: {started}",
                inline=True
            )
        
        await message.channel.send(embed=embed)

    async def handle_task_assign(self, message, parts):
        """Handle !task assign command"""
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 3:
            await message.channel.send("Usage: !task assign @user <task>")
            return
        
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        task_name = " ".join(parts[2:])
        
        target = guild.get_member(user_id)
        if not target:
            await message.channel.send("User not found!")
            return
        
        if guild_id not in self._tasks:
            self._tasks[guild_id] = {}
        
        self._tasks[guild_id][user_id] = {
            "task": task_name,
            "assigned_by": str(message.author),
            "assigned_at": time.time(),
            "completed": False
        }
        
        await self.log_action(guild_id, user_id, "task_assigned", task_name)
        
        await target.send(f"📋 New task assigned: {task_name}")
        await message.channel.send(f"✅ Task assigned to {target.display_name}")

    async def handle_task_complete(self, message, parts):
        """Handle !task complete command"""
        guild = message.guild
        guild_id = guild.id
        user_id = message.author.id
        
        if guild_id in self._tasks and user_id in self._tasks[guild_id]:
            task_data = self._tasks[guild_id][user_id]
            self._tasks[guild_id][user_id]["completed"] = True
            self._tasks[guild_id][user_id]["completed_at"] = time.time()
            
            task_name = task_data.get("task", "Unknown")
            await self.log_action(guild_id, user_id, "task_completed", task_name)
            
            await message.channel.send(f"✅ Task completed: {task_data.get('task', 'Unknown')}")
            
            if message.author.guild_permissions.administrator:
                xp_bonus = 50
                current_xp = dm.get_guild_data(guild_id, f"xp_{user_id}", 0)
                dm.update_guild_data(guild_id, f"xp_{user_id}", current_xp + xp_bonus)
                await message.channel.send(f"+{xp_bonus} XP bonus!")
        else:
            await message.channel.send("No task assigned to you!")

    async def handle_task_list(self, message):
        """Handle !tasks command"""
        guild = message.guild
        guild_id = guild.id
        
        tasks = self._tasks.get(guild_id, {})
        
        if not tasks:
            await message.channel.send("No active tasks!")
            return
        
        embed = discord.Embed(
            title="📋 Active Tasks",
            color=discord.Color.blue()
        )
        
        for user_id, task_data in tasks.items():
            member = guild.get_member(user_id)
            name = member.display_name if member else "Unknown"
            status = "✅ Done" if task_data.get("completed") else "⏳ Pending"
            embed.add_field(
                name=f"{name} ({status})",
                value=task_data.get("task", "Unknown"),
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_warn(self, message, parts):
        """Handle !warn command"""
        if not message.author.guild_permissions.administrator:
            await message.channel.send("Admin only!")
            return
        
        guild = message.guild
        
        if len(parts) < 3:
            await message.channel.send("Usage: !warn @user <reason>")
            return
        
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        reason = " ".join(parts[2:])
        
        target = guild.get_member(user_id)
        if not target:
            await message.channel.send("User not found!")
            return
        
        guild_id = guild.id
        
        if guild_id not in self._warnings:
            self._warnings[guild_id] = {}
        
        if user_id not in self._warnings[guild_id]:
            self._warnings[guild_id] = {"warnings": [], "active": True}
        
        self._warnings[guild_id]["warnings"].append({
            "reason": reason,
            "given_by": str(message.author),
            "timestamp": time.time()
        })
        
        await self.log_action(guild_id, user_id, "warn", reason)
        
        warning_count = len(self._warnings[guild_id]["warnings"])
        
        self._save_data()
        
        await target.send(f"⚠️ Warning given: {reason}\n\nTotal warnings: {warning_count}")
        
        await message.channel.send(f"⚠️ Warned {target.display_name}")
        
        if warning_count >= 3:
            await self._auto_demote(guild, target)
        elif warning_count == 2:
            await message.channel.send("⚠️ 2 warnings! Next warning = demotion.")

    async def handle_warnings(self, message, parts):
        """Handle !warnings command"""
        guild = message.guild
        guild_id = guild.id
        
        target = message.author
        if len(parts) > 1:
            user_mention = parts[1]
            try:
                user_id = int(user_mention.replace("<@", "").replace(">", ""))
                target = guild.get_member(user_id)
            except:
                pass
        
        if not target:
            await message.channel.send("User not found!")
            return
        
        warnings = self._warnings.get(guild_id, {}).get(target.id, {})
        
        if not warnings:
            await message.channel.send(f"{target.display_name} has no warnings!")
            return
        
        embed = discord.Embed(
            title=f"⚠️ Warnings: {target.display_name}",
            color=discord.Color.red()
        )
        
        for i, warn in enumerate(warnings.get("warnings", [])[-5:], 1):
            date = datetime.fromtimestamp(warn.get("timestamp", 0)).strftime("%m/%d")
            embed.add_field(
                name=f"Warning #{i}",
                value=f"{warn.get('reason', 'Unknown')}\nBy: {warn.get('given_by', 'Unknown')} | {date}",
                inline=False
            )
        
        await message.channel.send(embed=embed)

    async def handle_warnings_clear(self, message, parts):
        """Handle !warnings clear command"""
        if not message.author.guild_permissions.administrator:
            await message.channel.send("Admin only!")
            return
        
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 2:
            await message.channel.send("Usage: !warnings clear @user")
            return
        
        user_mention = parts[1]
        try:
            user_id = int(user_mention.replace("<@", "").replace(">", ""))
        except:
            await message.channel.send("Invalid user!")
            return
        
        if guild_id in self._warnings and user_id in self._warnings[guild_id]:
            del self._warnings[guild_id][user_id]
            self._save_data()
            await message.channel.send("✅ Warnings cleared!")
        else:
            await message.channel.send("No warnings found!")

    async def _auto_demote(self, guild, member):
        """Auto-demote after 3 warnings"""
        config = dm.get_guild_data(guild.id, "staff_promo_config", {})
        tiers = config.get("tiers", [])
        
        if tiers:
            current_tier_idx = 0
            for i, tier in enumerate(tiers):
                role_name = tier.get("role_name", "")
                if any(r.name == role_name for r in member.roles):
                    current_tier_idx = i
                    break
            
            if current_tier_idx > 0:
                new_tier = tiers[current_tier_idx - 1]
                new_role = discord.utils.get(guild.roles, name=new_tier.get("role_name", ""))
                old_role = discord.utils.get(guild.roles, name=tiers[current_tier_idx].get("role_name", ""))
                
                if old_role:
                    await member.remove_roles(old_role)
                if new_role:
                    await member.add_roles(new_role)
                
                try:
                    await member.send(f"📉 Demoted to {new_tier.get('name', 'Staff')} due to 3 warnings.")
                except:
                    pass
                
                channel_id = config.get("log_channel")
                if channel_id:
                    log_channel = guild.get_channel(channel_id)
                    if log_channel:
                        await log_channel.send(f"📉 {member.display_name} auto-demoted to {new_tier.get('name', 'Staff')} (3 warnings)")

    async def log_action(self, guild_id: int, user_id: int, action_type: str, details: str = ""):
        if guild_id not in self._activity_logs:
            self._activity_logs[guild_id] = []
        
        self._activity_logs[guild_id].append({
            "user_id": user_id,
            "action": action_type,
            "details": details,
            "timestamp": time.time()
        })
        
        self._activity_logs[guild_id] = self._activity_logs[guild_id][-200:]
        self._save_data()

    async def handle_activity_logs(self, message, parts):
        guild = message.guild
        guild_id = guild.id
        
        target = message.author
        if len(parts) > 1:
            user_mention = parts[1]
            try:
                user_id = int(user_mention.replace("<@", "").replace(">", ""))
                target = guild.get_member(user_id)
            except:
                pass
        
        if not target:
            await message.channel.send("User not found!")
            return
        
        logs = self._activity_logs.get(guild_id, [])
        user_logs = [l for l in logs if l.get("user_id") == target.id]
        
        if not user_logs:
            await message.channel.send(f"No activity logs for {target.display_name}!")
            return
        
        embed = discord.Embed(
            title=f"📋 Activity Log: {target.display_name}",
            color=discord.Color.blue()
        )
        
        warnings_given = sum(1 for l in user_logs if l.get("action") == "warn")
        tasks_assigned = sum(1 for l in user_logs if l.get("action") == "task_assigned")
        tasks_completed = sum(1 for l in user_logs if l.get("action") == "task_completed")
        shifts_worked = sum(1 for l in user_logs if l.get("action") == "shift")
        
        embed.add_field(name="Warnings Given", value=str(warnings_given), inline=True)
        embed.add_field(name="Tasks Assigned", value=str(tasks_assigned), inline=True)
        embed.add_field(name="Tasks Completed", value=str(tasks_completed), inline=True)
        embed.add_field(name="Shifts Worked", value=str(shifts_worked), inline=True)
        
        recent = user_logs[-10:]
        log_text = "\n".join([
            f"{datetime.fromtimestamp(l.get('timestamp', 0)).strftime('%m/%d %H:%M')} - {l.get('action', 'Unknown')}"
            for l in recent
        ])
        embed.add_field(name="Recent Actions", value=log_text or "No actions", inline=False)
        
        await message.channel.send(embed=embed)

    async def handle_all_activity(self, message):
        guild = message.guild
        guild_id = guild.id
        
        logs = self._activity_logs.get(guild_id, [])
        
        if not logs:
            await message.channel.send("No activity logs yet!")
            return
        
        action_counts = {}
        for log in logs:
            action = log.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
        
        embed = discord.Embed(
            title="📊 All Staff Activity",
            color=discord.Color.blue()
        )
        
        for action, count in sorted(action_counts.items(), key=lambda x: x[1], reverse=True):
            embed.add_field(name=action.replace("_", " ").title(), value=str(count), inline=True)
        
        await message.channel.send(embed=embed)


def setup(bot):
    return StaffShiftSystem(bot)