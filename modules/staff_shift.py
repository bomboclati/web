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
        self._load_data()

    def _load_data(self):
        data = dm.load_json("staff_shifts_tasks", default={})
        self._shifts = data.get("shifts", {})
        self._tasks = data.get("tasks", {})
        self._warnings = data.get("warnings", {})

    def _save_data(self):
        data = {
            "shifts": self._shifts,
            "tasks": self._tasks,
            "warnings": self._warnings
        }
        dm.save_json("staff_shifts_tasks", data)

    async def handle_shift_claim(self, message, parts):
        """Handle !shift claim command"""
        guild = message.guild
        guild_id = guild.id
        
        if len(parts) < 2:
            await message.channel.send("Usage: !shift claim <time>")
            return
        
        shift_time = parts[1]
        user_id = message.author.id
        
        if guild_id not in self._shifts:
            self._shifts[guild_id] = {}
        
        self._shifts[guild_id][user_id] = {
            "shift": shift_time,
            "claimed_at": time.time(),
            "claimed_by": str(message.author)
        }
        
        self._save_data()
        
        await message.channel.send(f"✅ Claimed shift: {shift_time}")

    async def handle_shift_coverage(self, message):
        """Handle !shift coverage command"""
        guild = message.guild
        guild_id = guild.id
        
        shifts = self._shifts.get(guild_id, {})
        
        if not shifts:
            await message.channel.send("No shifts currently claimed!")
            return
        
        embed = discord.Embed(
            title="📅 Shift Coverage",
            color=discord.Color.blue()
        )
        
        for user_id, shift_data in shifts.items():
            member = guild.get_member(user_id)
            name = member.display_name if member else shift_data.get("claimed_by", "Unknown")
            embed.add_field(
                name=name,
                value=shift_data.get("shift", "Unknown"),
                inline=True
            )
        
        await message.channel.send(embed=embed)

    async def handle_shift_drop(self, message):
        """Handle !shift drop command"""
        guild = message.guild
        guild_id = guild.id
        user_id = message.author.id
        
        if guild_id in self._shifts and user_id in self._shifts[guild_id]:
            del self._shifts[guild_id][user_id]
            self._save_data()
            await message.channel.send("✅ Shift dropped!")
        else:
            await message.channel.send("You don't have a shift claimed!")

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
        
        self._save_data()
        
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
            self._save_data()
            
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


def setup(bot):
    return StaffShiftSystem(bot)