import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from data_manager import dm
from logger import logger


class StaffReviewSystem:
    def __init__(self, bot):
        self.bot = bot
        self._reviews: Dict[int, Dict[int, dict]] = {}
        self._probation: Dict[int, Dict[int, dict]] = {}
        self._votes: Dict[int, List[dict]] = {}
        self._alerts_sent: Dict[int, float] = {}
        self._load_data()

    def _load_data(self):
        data = dm.load_json("staff_reviews", default={})
        self._reviews = data.get("reviews", {})
        self._probation = data.get("probation", {})
        self._votes = data.get("votes", {})
        self._alerts_sent = data.get("alerts_sent", {})

    def _save_data(self):
        data = {
            "reviews": self._reviews,
            "probation": self._probation,
            "votes": self._votes,
            "alerts_sent": self._alerts_sent
        }
        dm.save_json("staff_reviews", data)

    def start_review_loop(self):
        asyncio.create_task(self._review_loop())

    async def _review_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            try:
                await asyncio.sleep(86400)
                for guild in self.bot.guilds:
                    await self._run_auto_review(guild)
            except Exception as e:
                logger.error(f"Review loop error: {e}")
            await asyncio.sleep(86400)

    async def _run_auto_review(self, guild: discord.Guild):
        config = dm.get_guild_data(guild.id, "staff_promo_config", {})
        
        if not config.get("auto_review", False):
            return
        
        channel_id = config.get("review_channel")
        if not channel_id:
            return
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        
        staff_role_ids = config.get("staff_roles", [])
        
        for role_id in staff_role_ids:
            role = guild.get_role(role_id)
            if not role:
                continue
            
            for member in role.members:
                if member.bot:
                    continue
                
                await self._generate_staff_report(guild, member, channel)

    async def _generate_staff_report(self, guild: discord.Guild, member: discord.Member, channel):
        user_id = member.id
        guild_id = guild.id
        
        stats = self._get_staff_stats(guild_id, user_id)
        
        messages = stats.get("messages", 0)
        xp = stats.get("xp", 0)
        achievements = stats.get("achievements", 0)
        join_date = member.joined_at
        
        days_since_join = (datetime.now() - join_date).days
        
        embed = discord.Embed(
            title=f"📊 Staff Review: {member.display_name}",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="Joined", value=f"{days_since_join} days ago", inline=True)
        embed.add_field(name="Messages", value=f"{messages:,}", inline=True)
        embed.add_field(name="XP", value=f"{xp:,}", inline=True)
        embed.add_field(name="Achievements", value=str(achievements), inline=True)
        
        performance_score = min(1.0, (messages / 5000) * 0.3 + (xp / 10000) * 0.4 + (achievements / 20) * 0.3)
        
        embed.add_field(
            name="📈 Performance Score",
            value=f"{performance_score:.0%}",
            inline=True
        )
        
        emoji = "🟢" if performance_score >= 0.7 else "🟡" if performance_score >= 0.4 else "🔴"
        
        embed.add_field(
            name="Status",
            value=f"{emoji} {'Excellent' if performance_score >= 0.7 else 'Needs Improvement' if performance_score >= 0.4 else 'At Risk'}",
            inline=True
        )
        
        embed.timestamp = datetime.now()
        
        await channel.send(embed=embed)
        
        view = discord.ui.View()
        
        approve_btn = discord.ui.Button(label="Approve", style=discord.ButtonStyle.success, custom_id=f"review_approve_{user_id}")
        improve_btn = discord.ui.Button(label="Needs Improvement", style=discord.ButtonStyle.secondary, custom_id=f"review_improve_{user_id}")
        hold_btn = discord.ui.Button(label="Hold", style=discord.ButtonStyle.danger, custom_id=f"review_hold_{user_id}")
        
        async def approve_callback(interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Admin only.", ephemeral=True)
                return
            await self._process_vote(guild, user_id, interaction.user.id, True)
            await interaction.response.send_message("✅ Approved!", ephemeral=True)
        
        async def improve_callback(interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Admin only.", ephemeral=True)
                return
            await self._process_vote(guild, user_id, interaction.user.id, False)
            await interaction.response.send_message("✅ Marked for improvement!", ephemeral=True)
        
        async def hold_callback(interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Admin only.", ephemeral=True)
                return
            await self._process_vote(guild, user_id, interaction.user.id, None)
            await interaction.response.send_message("✅ On hold!", ephemeral=True)
        
        approve_btn.callback = approve_callback
        improve_btn.callback = improve_callback
        hold_btn.callback = hold_callback
        
        view.add_item(approve_btn)
        view.add_item(improve_btn)
        view.add_item(hold_btn)
        
        await channel.send("Vote:", view=view)

    def _get_staff_stats(self, guild_id: int, user_id: int) -> dict:
        return {
            "messages": dm.get_guild_data(guild_id, f"messages_{user_id}", 0),
            "xp": dm.get_guild_data(guild_id, f"xp_{user_id}", 0),
            "achievements": dm.get_guild_data(guild_id, f"achievements_{user_id}", 0),
            "tickets": dm.get_guild_data(guild_id, f"tickets_{user_id}", 0),
            "rep": dm.get_guild_data(guild_id, f"rep_{user_id}", 0),
        }

    async def _process_vote(self, guild, user_id, voter_id, vote):
        guild_id = guild.id
        
        if guild_id not in self._votes:
            self._votes[guild_id] = []
        
        self._votes[guild_id].append({
            "user_id": user_id,
            "voter_id": voter_id,
            "vote": vote,
            "timestamp": time.time()
        })
        
        self._votes[guild_id] = self._votes[guild_id][-50:]
        self._save_data()

    async def start_probation(self, guild, member, days=14):
        guild_id = guild.id
        
        if guild_id not in self._probation:
            self._probation[guild_id] = {}
        
        self._probation[guild_id][member.id] = {
            "start_time": time.time(),
            "duration_days": days,
            "completed": False,
            "requirements": {
                "shadow_hours": 2,
                "training_completed": False,
                "introduced": False
            }
        }
        
        self._save_data()
        
        try:
            await member.send(f"🎓 Welcome to your {days}-day probation period!\n\nRequired:\n1. Shadow a senior staff for 2 hours\n2. Complete training modules\n3. Introduce yourself in #staff-chat\n\nGood luck!")
        except:
            pass

    async def check_probation(self, guild, member) -> dict:
        guild_id = guild.id
        probation = self._probation.get(guild_id, {}).get(member.id)
        
        if not probation:
            return {"status": "not_on_probation"}
        
        start_time = probation.get("start_time")
        duration = probation.get("duration_days", 14) * 86400
        elapsed = time.time() - start_time
        
        if elapsed >= duration:
            if not probation.get("completed"):
                return {"status": "ready_for_review", "days_left": 0}
            else:
                return {"status": "passed", "days_left": 0}
        
        days_left = int((duration - elapsed) / 86400)
        return {"status": "active", "days_left": days_left, "requirements": probation.get("requirements", {})}

    async def send_performance_alert(self, guild, member, alert_type: str, score: float, next_tier: str):
        guild_id = guild.id
        
        last_alert = self._alerts_sent.get(guild_id, {}).get(member.id, 0)
        
        if time.time() - last_alert < 86400:
            return
        
        try:
            if alert_type == "near_promotion":
                embed = discord.Embed(
                    title="📈 Near Promotion!",
                    description=f"You're close to becoming {next_tier}!",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Current Score",
                    value=f"{score:.0%}",
                    inline=True
                )
                embed.add_field(
                    name="Need",
                    value=f"+{(next_tier and 0.1 or 0.05):.0%} more",
                    inline=True
                )
                embed.add_field(
                    name="Tip",
                    value="Complete more tasks to reach the next tier!",
                    inline=False
                )
            
            elif alert_type == "near_demotion":
                embed = discord.Embed(
                    title="⚠️ At Risk of Demotion",
                    description=f"Your performance is dropping!",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="Current Score",
                    value=f"{score:.0%}",
                    inline=True
                )
                embed.add_field(
                    name="Action Needed",
                    value="Increase activity to avoid demotion!",
                    inline=True
                )
            
            await member.send(embed=embed)
            
            if guild_id not in self._alerts_sent:
                self._alerts_sent[guild_id] = {}
            self._alerts_sent[guild_id][member.id] = time.time()
            self._save_data()
        
        except:
            pass

    async def get_staff_stats(self, guild, member) -> dict:
        user_id = member.id
        guild_id = guild.id
        
        stats = self._get_staff_stats(guild_id, user_id)
        
        join_date = member.joined_at
        days_since_join = (datetime.now() - join_date).days
        
        messages = stats.get("messages", 0)
        xp = stats.get("xp", 0)
        achievements = stats.get("achievements", 0)
        
        performance_score = min(1.0, (messages / 5000) * 0.3 + (xp / 10000) * 0.4 + (achievements / 20) * 0.3)
        
        votes = self._votes.get(guild_id, [])
        staff_votes = [v for v in votes if v.get("user_id") == user_id]
        approve_count = sum(1 for v in staff_votes if v.get("vote") == True)
        reject_count = sum(1 for v in staff_votes if v.get("vote") == False)
        
        return {
            "member": member,
            "join_date": join_date.strftime("%Y-%m-%d"),
            "days_active": days_since_join,
            "messages": messages,
            "xp": xp,
            "achievements": achievements,
            "performance_score": performance_score,
            "approve_votes": approve_count,
            "reject_votes": reject_count,
            "total_votes": len(staff_votes)
        }

    async def handle_peer_vote(self, message):
        parts = message.content.split()
        
        if len(parts) < 2:
            await message.channel.send("Usage: !vote @user [yes/no]")
            return
        
        guild = message.guild
        
        try:
            user_mention = parts[1]
            user_id = int(user_mention.replace("<@", "").replace(">", ""))
        except:
            await message.channel.send("Invalid user mention!")
            return
        
        vote = parts[2].lower() if len(parts) > 2 else "yes"
        vote_bool = vote in ["yes", "approve", "y", "true"]
        
        await self._process_vote(guild, user_id, message.author.id, vote_bool)
        
        await message.channel.send(f"✅ Voted on {message.guild.get_member(user_id).display_name}!")

    async def handle_staff_stats(self, message, parts):
        guild = message.guild
        
        target = message.author
        if len(parts) > 1:
            try:
                user_mention = parts[1]
                user_id = int(user_mention.replace("<@", "").replace(">", ""))
                target = guild.get_member(user_id)
            except:
                pass
        
        if not target:
            await message.channel.send("User not found!")
            return
        
        stats = await self.get_staff_stats(guild, target)
        
        embed = discord.Embed(
            title=f"📊 Stats: {stats['member'].display_name}",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Joined", value=stats["join_date"], inline=True)
        embed.add_field(name="Days Active", value=str(stats["days_active"]), inline=True)
        embed.add_field(name="Messages", value=f"{stats['messages']:,}", inline=True)
        embed.add_field(name="XP", value=f"{stats['xp']:,}", inline=True)
        embed.add_field(name="Achievements", value=str(stats["achievements"]), inline=True)
        embed.add_field(name="Performance", value=f"{stats['performance_score']:.0%}", inline=True)
        embed.add_field(name="Votes", value=f"✅ {stats['approve_votes']} | ❌ {stats['reject_votes']}", inline=True)
        
        await message.channel.send(embed=embed)

    async def handle_probation_status(self, message, parts):
        guild = message.guild
        
        target = message.author
        if len(parts) > 1:
            try:
                user_mention = parts[1]
                user_id = int(user_mention.replace("<@", "").replace(">", ""))
                target = guild.get_member(user_id)
            except:
                pass
        
        if not target:
            await message.channel.send("User not found!")
            return
        
        status = await self.check_probation(guild, target)
        
        if status.get("status") == "not_on_probation":
            await message.channel.send(f"{target.display_name} is not on probation.")
            return
        
        embed = discord.Embed(
            title=f"🎓 Probation: {target.display_name}",
            color=discord.Color.orange()
        )
        
        embed.add_field(name="Status", value=status.get("status", "unknown").replace("_", " ").title(), inline=True)
        embed.add_field(name="Days Left", value=str(status.get("days_left", 0)), inline=True)
        
        requirements = status.get("requirements", {})
        req_text = "\n".join([f"□ {k.replace('_', ' ').title()}" for k, v in requirements.items() if not v])
        embed.add_field(name="Requirements", value=req_text or "All complete!", inline=False)
        
        await message.channel.send(embed=embed)


def setup(bot):
    return StaffReviewSystem(bot)