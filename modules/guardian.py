"""
Guardian: AI-Powered Anti-Raid Shield for Miro Bot
Detects and mitigates raid attempts in real-time using behavioral analysis and AI.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Dict, List, Optional, Set
import asyncio
import time
from datetime import datetime, timedelta
import logging
from collections import defaultdict, Counter
import re

from data_manager import dm

logger = logging.getLogger(__name__)

# Raid detection thresholds
RAID_CONFIG = {
    'join_rate_threshold': 10,  # Users joining per minute to trigger alert
    'message_rate_threshold': 20,  # Messages per minute per user to flag
    'account_age_threshold': timedelta(days=7),  # Minimum account age
    'duplicate_message_threshold': 3,  # Similar messages to flag as spam
    'ai_confidence_threshold': 0.75,  # AI confidence for raid detection
    'lockdown_duration': 300,  # 5 minutes default lockdown
}

class GuardianSystem:
    def __init__(self, bot):
        self.bot = bot
        self.join_times: Dict[int, List[datetime]] = defaultdict(list)
        self.message_counts: Dict[int, Dict[int, List[datetime]]] = defaultdict(lambda: defaultdict(list))
        self.suspicious_users: Set[int] = set()
        self.raid_mode_active: Dict[int, bool] = defaultdict(bool)
        self.lockdown_end_times: Dict[int, datetime] = {}
        self.captcha_challenges: Dict[int, dict] = {}  # user_id -> challenge data
        self.verified_users: Set[int] = set()
        self._load_config()
        
    def _load_config(self):
        """Load guardian configuration from database"""
        try:
            config = dm.load_json("guardian_config", default={})
            for guild_id, guild_config in config.items():
                RAID_CONFIG.update(guild_config)
        except Exception as e:
            logger.error(f"Failed to load guardian config: {e}")

    async def check_join_rate(self, guild: discord.Guild) -> bool:
        """Check if join rate exceeds threshold"""
        now = discord.utils.utcnow()
        one_minute_ago = now - timedelta(minutes=1)
        
        # Clean old entries
        self.join_times[guild.id] = [
            t for t in self.join_times[guild.id] 
            if t > one_minute_ago
        ]
        
        # Check threshold
        if len(self.join_times[guild.id]) >= RAID_CONFIG['join_rate_threshold']:
            logger.warning(f"Raid detected in {guild.name}: {len(self.join_times[guild.id])} joins/min")
            return True
        return False

    async def check_message_behavior(self, message: discord.Message) -> Optional[str]:
        """Analyze message for spam/raid behavior"""
        if message.author.bot or message.guild is None:
            return None
            
        guild_id = message.guild.id
        user_id = message.author.id
        now = discord.utils.utcnow()
        
        # Track message timing
        self.message_counts[guild_id][user_id].append(now)
        
        # Clean old entries (keep last minute)
        one_minute_ago = now - timedelta(minutes=1)
        self.message_counts[guild_id][user_id] = [
            t for t in self.message_counts[guild_id][user_id]
            if t > one_minute_ago
        ]
        
        msg_count = len(self.message_counts[guild_id][user_id])
        
        # Check message rate
        if msg_count >= RAID_CONFIG['message_rate_threshold']:
            return "high_message_rate"
        
        # Check for duplicate messages (simple hash comparison)
        recent_msgs = []
        for mid in range(max(0, len(self.bot.cached_messages) - 50), len(self.bot.cached_messages)):
            if hasattr(self.bot.cached_messages[mid], 'content'):
                recent_msgs.append(self.bot.cached_messages[mid].content)
        
        similar_count = sum(
            1 for msg in recent_msgs[-10:]
            if self._similarity_score(message.content, msg) > 0.9
        )
        
        if similar_count >= RAID_CONFIG['duplicate_message_threshold']:
            return "duplicate_spam"
        
        return None

    def _similarity_score(self, s1: str, s2: str) -> float:
        """Simple string similarity score"""
        if not s1 or not s2:
            return 0.0
        s1, s2 = s1.lower(), s2.lower()
        if s1 == s2:
            return 1.0
        # Simple word overlap
        words1 = set(s1.split())
        words2 = set(s2.split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    async def check_account_age(self, member: discord.Member) -> bool:
        """Check if account is too new"""
        account_age = discord.utils.utcnow() - member.created_at
        return account_age < RAID_CONFIG['account_age_threshold']

    async def initiate_lockdown(self, guild: discord.Guild, reason: str = "Raid detected"):
        """Put guild into lockdown mode"""
        if guild.id in self.lockdown_end_times:
            if self.lockdown_end_times[guild.id] > discord.utils.utcnow():
                return  # Already in lockdown
        
        logger.info(f"Initiating lockdown for {guild.name}: {reason}")
        
        # Disable sending messages in all channels
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    guild.default_role,
                    send_messages=False,
                    add_reactions=False
                )
            except discord.Forbidden:
                logger.warning(f"Cannot lockdown channel {channel.name}")
        
        self.raid_mode_active[guild.id] = True
        self.lockdown_end_times[guild.id] = discord.utils.utcnow() + timedelta(seconds=RAID_CONFIG['lockdown_duration'])
        
        # Notify admins
        admin_channel = await self._find_admin_channel(guild)
        if admin_channel:
            embed = discord.Embed(
                title="🚨 RAID LOCKDOWN ACTIVATED",
                description=f"**Reason:** {reason}\n**Duration:** {RAID_CONFIG['lockdown_duration']}s",
                color=discord.Color.red()
            )
            embed.add_field(name="Auto-moderation", value="Message sending restricted for non-admins")
            embed.add_field(name="Captcha verification", value="Enabled for new users")
            await admin_channel.send(embed=embed)
        
        # Schedule auto-unlock
        asyncio.create_task(self._auto_unlock(guild))

    async def _auto_unlock(self, guild: discord.Guild):
        """Automatically unlock after duration"""
        await asyncio.sleep(RAID_CONFIG['lockdown_duration'])
        
        if guild.id in self.lockdown_end_times:
            del self.lockdown_end_times[guild.id]
        
        self.raid_mode_active[guild.id] = False
        
        # Restore permissions
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    guild.default_role,
                    send_messages=None,
                    add_reactions=None
                )
            except discord.Forbidden:
                pass
        
        logger.info(f"Lockdown lifted for {guild.name}")

    async def _find_admin_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Find a channel where admins hang out"""
        for channel in guild.text_channels:
            if any(role.permissions.administrator for role in guild.roles if role.name.lower() in ['admin', 'moderator']):
                return channel
        return guild.system_channel

    async def send_captcha(self, member: discord.Member) -> bool:
        """Send captcha challenge to user"""
        import random
        import string
        
        # Generate simple math captcha
        num1 = random.randint(1, 20)
        num2 = random.randint(1, 20)
        answer = num1 + num2
        
        self.captcha_challenges[member.id] = {
            'answer': answer,
            'expires': discord.utils.utcnow() + timedelta(minutes=5),
            'attempts': 0
        }
        
        try:
            embed = discord.Embed(
                title="🛡️ Security Verification Required",
                description=f"To verify you're human, please solve:\n\n**{num1} + {num2} = ?**",
                color=discord.Color.blue()
            )
            embed.add_field(name="Time limit", value="5 minutes")
            embed.set_footer(text="Reply with just the number")
            
            await member.send(embed=embed)
            return True
        except discord.Forbidden:
            # Can't DM user, kick them
            try:
                await member.kick(reason="Failed captcha verification (cannot DM)")
            except:
                pass
            return False

    async def verify_captcha(self, user_id: int, answer: str) -> bool:
        """Verify captcha answer"""
        if user_id not in self.captcha_challenges:
            return False
        
        challenge = self.captcha_challenges[user_id]
        
        # Check expiration
        if discord.utils.utcnow() > challenge['expires']:
            del self.captcha_challenges[user_id]
            return False
        
        # Check answer
        try:
            if int(answer.strip()) == challenge['answer']:
                self.verified_users.add(user_id)
                del self.captcha_challenges[user_id]
                return True
            else:
                challenge['attempts'] += 1
                if challenge['attempts'] >= 3:
                    del self.captcha_challenges[user_id]
                    return False
                return False
        except ValueError:
            return False

    async def ai_analyze_message(self, message: discord.Message) -> float:
        """Use AI to analyze message for raid/spam content"""
        if not hasattr(self.bot, 'ai') or self.bot.ai is None:
            return 0.0
        
        prompt = f"""Analyze this Discord message for raid/spam behavior. 
Rate from 0.0 (safe) to 1.0 (definitely raid/spam).
Consider: repetitive content, malicious links, excessive caps, suspicious patterns.

Message: "{message.content}"
Author Account Age: {(discord.utils.utcnow() - message.author.created_at).days} days
Is New Member: {(discord.utils.utcnow() - message.author.joined_at).total_seconds() < 3600 if message.author.joined_at else True}

Respond with ONLY a number between 0.0 and 1.0."""

        try:
            # Using MiroBot's ai client which returns a dict
            res = await self.bot.ai.chat(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_input=prompt,
                system_prompt="You are a security AI. Respond only with a number."
            )
            response = res.get("summary", "0.0")
            
            # Extract number from response
            import re
            numbers = re.findall(r'\d+\.?\d*', response)
            if numbers:
                return min(1.0, max(0.0, float(numbers[0])))
            return 0.0
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return 0.0

    async def handle_suspicious_user(self, member: discord.Member, reason: str):
        """Handle a flagged suspicious user"""
        self.suspicious_users.add(member.id)
        
        # Log incident
        logger.warning(f"Suspicious user {member.id} ({member.name}): {reason}")
        
        # Auto-kick if high risk
        if reason in ["high_message_rate", "duplicate_spam"]:
            ai_score = await self.ai_analyze_message(
                type('FakeMessage', (), {'content': 'spam', 'author': member, 'created_at': discord.utils.utcnow()})()
            )
            if ai_score > RAID_CONFIG['ai_confidence_threshold']:
                try:
                    await member.kick(reason=f"Auto-kicked by Guardian: {reason}")
                    
                    # Notify mods
                    admin_channel = await self._find_admin_channel(member.guild)
                    if admin_channel:
                        embed = discord.Embed(
                            title="⚠️ User Auto-Kicked",
                            description=f"**User:** {member.mention}\n**Reason:** {reason}",
                            color=discord.Color.orange()
                        )
                        embed.add_field(name="AI Confidence", value=f"{ai_score:.0%}")
                        await admin_channel.send(embed=embed)
                except discord.Forbidden:
                    pass

class GuardianCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.guardian = GuardianSystem(bot)
        self.bot.guardian = self.guardian
        
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member joins"""
        if member.bot:
            return
            
        # Track join time
        self.guardian.join_times[member.guild.id].append(discord.utils.utcnow())
        
        # Check for raid
        if await self.guardian.check_join_rate(member.guild):
            await self.guardian.initiate_lockdown(member.guild, "High join rate detected")
        
        # Check account age
        if await self.guardian.check_account_age(member):
            # Send captcha
            if not await self.guardian.send_captcha(member):
                # Kick if can't send captcha
                try:
                    await member.kick(reason="New account, captcha verification failed")
                except:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Monitor messages for spam/raid behavior"""
        if message.author.bot or not message.guild:
            return
        
        # Skip verified users
        if message.author.id in self.guardian.verified_users:
            return
        
        # Check if in lockdown
        if self.guardian.raid_mode_active.get(message.guild.id, False):
            # Allow only admins during lockdown
            if not message.author.guild_permissions.administrator:
                await message.delete()
                return
        
        # Analyze message
        behavior_flag = await self.guardian.check_message_behavior(message)
        if behavior_flag:
            await self.guardian.handle_suspicious_user(message.author, behavior_flag)
            await message.delete()
            return
        
        # AI analysis for suspicious accounts
        if (discord.utils.utcnow() - message.author.created_at).days < 30:
            ai_score = await self.guardian.ai_analyze_message(message)
            if ai_score > RAID_CONFIG['ai_confidence_threshold']:
                await self.guardian.handle_suspicious_user(message.author, f"AI detected spam (score: {ai_score:.2f})")
                await message.delete()

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Monitor edited messages"""
        if after.author.bot or not after.guild:
            return
        
        # Re-analyze edited messages from new accounts
        if (discord.utils.utcnow() - after.author.created_at).days < 7:
            ai_score = await self.guardian.ai_analyze_message(after)
            if ai_score > RAID_CONFIG['ai_confidence_threshold'] * 1.2:  # Higher threshold for edits
                await self.guardian.handle_suspicious_user(after.author, "AI detected spam in edited message")
                await after.delete()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle captcha reactions if implemented"""
        pass

    @commands.command(name="guardian", aliases=["guard", "shield"])
    @commands.has_permissions(administrator=True)
    async def guardian_command(self, ctx, action: str = "status", duration: int = 300):
        """Guardian control panel - !guardian [status|lockdown|unlock] [duration]"""
        guild = ctx.guild
        
        if action == "status":
            in_lockdown = self.guardian.raid_mode_active.get(guild.id, False)
            embed = discord.Embed(
                title="🛡️ Guardian Status",
                color=discord.Color.green() if not in_lockdown else discord.Color.red()
            )
            embed.add_field(name="Lockdown Active", value="✅ Yes" if in_lockdown else "❌ No")
            embed.add_field(name="Verified Users", value=len(self.guardian.verified_users))
            embed.add_field(name="Suspicious Users", value=len(self.guardian.suspicious_users))
            
            if in_lockdown and guild.id in self.guardian.lockdown_end_times:
                remaining = self.guardian.lockdown_end_times[guild.id] - discord.utils.utcnow()
                embed.add_field(name="Time Remaining", value=f"{max(0, int(remaining.total_seconds()))}s")
            
            await ctx.send(embed=embed)
            
        elif action == "lockdown":
            RAID_CONFIG['lockdown_duration'] = duration
            await self.guardian.initiate_lockdown(guild, "Manual lockdown by admin")
            await ctx.send("🔒 Lockdown initiated!", delete_after=5)
            
        elif action == "unlock":
            self.guardian.raid_mode_active[guild.id] = False
            if guild.id in self.guardian.lockdown_end_times:
                del self.guardian.lockdown_end_times[guild.id]
            
            # Restore permissions
            for channel in guild.text_channels:
                try:
                    await channel.set_permissions(guild.default_role, send_messages=None, add_reactions=None)
                except:
                    pass
            
            await ctx.send("🔓 Lockdown lifted!", delete_after=5)
        else:
            await ctx.send("Usage: `!guardian [status|lockdown|unlock] [duration_seconds]`")

    @commands.command(name="verify", aliases=["captcha"])
    async def verify_command(self, ctx, answer: str):
        """Submit captcha answer - !verify <answer>"""
        success = await self.guardian.verify_captcha(ctx.author.id, answer)
        
        if success:
            embed = discord.Embed(
                title="✅ Verification Successful",
                description="You are now verified and can participate normally.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed, delete_after=10)
        else:
            embed = discord.Embed(
                title="❌ Verification Failed",
                description="Incorrect answer or challenge expired. Please wait for a new challenge.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed, delete_after=10)

    @verify_command.error
    async def verify_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send("You don't have a pending captcha challenge.", delete_after=5)

async def setup(bot):
    await bot.add_cog(GuardianCog(bot))
    logger.info("Guardian anti-raid system loaded")
