import asyncio
import time
from datetime import datetime, timedelta
import json
import discord
from discord.ext import commands, tasks

from data_manager import dm
from logger import logger


class StaffPromotionSystem:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        self._default_tiers = [
            {"name": "Trial Moderator", "threshold": 0.2, "role_name": "Trial Moderator"},
            {"name": "Moderator", "threshold": 0.4, "role_name": "Moderator"},
            {"name": "Senior Moderator", "threshold": 0.6, "role_name": "Senior Moderator"},
            {"name": "Head Moderator", "threshold": 0.8, "role_name": "Head Moderator"},
            {"name": "Admin", "threshold": 0.95, "role_name": "Admin"},
        ]
        
        self._default_metrics = {
            "xp": {"weight": 0.25, "max": 5000, "enabled": True},
            "tenure_days": {"weight": 0.20, "max": 90, "enabled": True},
            "messages": {"weight": 0.15, "max": 1000, "enabled": True},
            "achievements": {"weight": 0.15, "max": 20, "enabled": True},
            "voice_minutes": {"weight": 0.10, "max": 3600, "enabled": True},
            "rep_received": {"weight": 0.08, "max": 100, "enabled": True},
            "rep_given": {"weight": 0.07, "max": 100, "enabled": True},
        }
        
        self._default_settings = {
            "auto_promote": True,
            "auto_demote": False,
            "min_tenure_hours": 72,
            "require_existing_role": None,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "notify_on_promotion": True,
            "announce_channel": None,
            "log_channel": None,
        }
        
        self._last_promotion_time = {}
        self._promotion_loop.start()

    def _get_full_config(self, guild_id: int) -> dict:
        cfg = dm.get_guild_data(guild_id, "staff_promo_config", {})
        cfg.setdefault("tiers", self._default_tiers)
        cfg.setdefault("metrics", self._default_metrics)
        cfg.setdefault("settings", self._default_settings)
        return cfg

    async def _promotion_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed:
            try:
                for guild in self.bot.guilds:
                    await self._evaluate_guild(guild)
            except Exception as e:
                logger.error(f"Staff promo loop error: {e}")
            await asyncio.sleep(3600)

    async def _evaluate_guild(self, guild: discord.Guild):
        config = self._get_full_config(guild.id)
        settings = config.get("settings", self._default_settings)
        
        if not settings.get("auto_promote", True):
            return
        
        tiers = config.get("tiers", self._default_tiers)
        metrics = config.get("metrics", self._default_metrics)
        excluded = settings.get("excluded_users", [])
        
        role_ids = {}
        for tier in tiers:
            role_name = tier.get("role_name")
            if role_name:
                r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                if r:
                    role_ids[tier["name"]] = r.id
        
        for member in guild.members:
            if member.bot or member.id in excluded:
                continue
            
            if not self._check_tenure(member, settings):
                continue
            
            await self._evaluate_member(guild, member, tiers, role_ids, metrics, settings)

    def _check_tenure(self, member: discord.Member, settings: dict) -> bool:
        min_hours = settings.get("min_tenure_hours", 72)
        if not member.joined_at:
            return False
        tenure_hours = (datetime.utcnow() - member.joined_at).total_seconds() / 3600
        return tenure_hours >= min_hours

    async def _evaluate_member(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, metrics, settings):
        user_id = member.id
        cooldown_key = f"{guild.id}_{user_id}"
        cooldown_hours = settings.get("promotion_cooldown_hours", 24)
        
        if cooldown_key in self._last_promotion_time:
            last = self._last_promotion_time[cooldown_key]
            if (datetime.utcnow() - last).total_seconds() < cooldown_hours * 3600:
                return
        
        score = self._compute_score(guild.id, user_id, member, metrics)
        target_tier = None
        for tier in sorted(tiers, key=lambda t: t.get("threshold", 0)):
            if score >= tier.get("threshold", 0):
                target_tier = tier
        
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        target_index = -1 if not target_tier else tiers.index(target_tier)
        
        if target_index > current_index:
            await self._promote_member(guild, member, target_tier, tiers, role_ids, current_index, settings)
            self._last_promotion_time[cooldown_key] = datetime.utcnow()

    def _get_current_tier_index(self, member: discord.Member, tiers, role_ids) -> int:
        for idx, tier in enumerate(tiers):
            rid = role_ids.get(tier.get("name"))
            if rid and any(r.id == rid for r in member.roles):
                return idx
        return -1

    def _compute_score(self, guild_id: int, user_id: int, member: discord.Member, metrics: dict) -> float:
        now = datetime.utcnow()
        joined = member.joined_at or now
        tenure_days = (now - joined).days
        
        udata = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        values = {
            "xp": udata.get("xp", 0),
            "tenure_days": tenure_days,
            "messages": udata.get("total_messages", 0),
            "achievements": len(dm.get_guild_data(guild_id, f"achievements_{user_id}", [])),
            "voice_minutes": udata.get("voice_minutes", 0),
            "rep_received": udata.get("rep_received", 0),
            "rep_given": udata.get("rep_given", 0),
        }
        
        score = 0.0
        for metric_name, config in metrics.items():
            if not config.get("enabled", True):
                continue
            weight = config.get("weight", 0)
            max_val = config.get("max", 100)
            raw_val = values.get(metric_name, 0)
            normalized = max(0, min(1, raw_val / max_val)) if max_val > 0 else 0
            score += normalized * weight
        
        return min(1.0, score)

    async def _promote_member(self, guild: discord.Guild, member: discord.Member, target_tier, tiers, role_ids, current_index, settings):
        new_role_id = role_ids.get(target_tier.get("name"))
        if new_role_id:
            try:
                role = guild.get_role(new_role_id)
                if role and role not in member.roles:
                    await member.add_roles(role)
            except Exception as e:
                logger.error(f"Failed to assign promotion role: {e}")
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            tier = tiers[idx]
            rid = role_ids.get(tier.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in member.roles:
                    try:
                        await member.remove_roles(rm)
                    except:
                        pass
        
        await self._log_promotion(guild, member, target_tier.get("name"), settings)

    async def _log_promotion(self, guild: discord.Guild, member: discord.Member, new_tier: str, settings: dict):
        logger.info(f"StaffPromo[{guild.id}] {member} promoted to {new_tier}")
        
        log_ch_id = settings.get("log_channel")
        if log_ch_id:
            channel = guild.get_channel(int(log_ch_id))
            if channel:
                try:
                    embed = discord.Embed(
                        title="🎖️ Staff Promotion",
                        description=f"{member.mention} has been promoted to **{new_tier}**",
                        color=discord.Color.green()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                except:
                    pass
        
        announce_ch_id = settings.get("announce_channel")
        if announce_ch_id and settings.get("notify_on_promotion", True):
            channel = guild.get_channel(int(announce_ch_id))
            if channel:
                try:
                    await channel.send(f"🎉 Congratulations {member.mention}! Promoted to **{new_tier}**!")
                except:
                    pass

    def get_config(self, guild_id: int) -> dict:
        return self._get_full_config(guild_id)

    async def setup(self, interaction: discord.Interaction, params: dict = None):
        guild = interaction.guild
        
        doc_name = "staff-promo-guide"
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            doc_channel = await guild.create_text_channel(doc_name, overwrites=overwrites)
        except:
            doc_channel = interaction.channel
        
        config = self._get_full_config(guild.id)
        tiers = config.get("tiers", self._default_tiers)
        metrics = config.get("metrics", self._default_metrics)
        
        embed = discord.Embed(
            title="🧭 Staff Auto-Promotion System",
            description="Automatically promotes staff members based on performance metrics",
            color=discord.Color.green()
        )
        
        tiers_text = "\n".join([f"• **{t['name']}**: {int(t['threshold']*100)}% score" for t in tiers])
        embed.add_field(name="📊 Promotion Tiers", value=tiers_text or "No tiers configured", inline=False)
        
        metrics_text = "\n".join([f"• **{k}**: {int(v['weight']*100)}% weight" for k, v in metrics.items() if v.get("enabled")])
        embed.add_field(name="📈 Scoring Metrics", value=metrics_text or "No metrics enabled", inline=False)
        
        embed.add_field(
            name="⚙️ Configuration",
            value=(
                f"• Auto-promote: `{config.get('settings', {}).get('auto_promote', True)}`\n"
                f"• Min tenure: `{config.get('settings', {}).get('min_tenure_hours', 72)} hours`\n"
                f"• Cooldown: `{config.get('settings', {}).get('promotion_cooldown_hours', 24)} hours`"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💬 Commands",
            value=(
                "• `!staffpromo status` - Check your current score\n"
                "• `!staffpromo leaderboard` - Top staff members\n"
                "• `!staffpromo config` - View configuration (admin)\n"
                "• `/bot staffpromo` - Open setup panel"
            ),
            inline=False
        )
        
        await doc_channel.send(embed=embed)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["staffpromo status"] = json.dumps({
            "command_type": "staffpromo_status"
        })
        custom_cmds["staffpromo leaderboard"] = json.dumps({
            "command_type": "staffpromo_leaderboard"
        })
        custom_cmds["staffpromo config"] = json.dumps({
            "command_type": "staffpromo_config"
        })
        
        custom_cmds["help staffpromo"] = json.dumps({
            "command_type": "help_embed",
            "title": "Staff Promotion System Help",
            "description": "Auto-promotes staff members based on performance metrics.",
            "fields": [
                {"name": "!staffpromo status", "value": "Check your current promotion score.", "inline": False},
                {"name": "!staffpromo leaderboard", "value": "View top staff members by score.", "inline": False},
                {"name": "!staffpromo config", "value": "View configuration (admin only).", "inline": False},
                {"name": "!help staffpromo", "value": "Show this help embed.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        await interaction.followup.send("✅ Staff Promotion System set up! Check the staff-promo-guide channel.", ephemeral=True)

    async def handle_status(self, message: discord.Message):
        guild = message.guild
        member = message.author
        config = self._get_full_config(guild.id)
        metrics = config.get("metrics", self._default_metrics)
        
        score = self._compute_score(guild.id, member.id, member, metrics)
        tiers = config.get("tiers", self._default_tiers)
        
        current_tier = "None"
        for tier in tiers:
            rid = config.get("roles_by_tier", {}).get(tier["name"])
            if rid and any(r.id == rid for r in member.roles):
                current_tier = tier["name"]
                break
        
        embed = discord.Embed(title="📊 Your Staff Promotion Status", color=discord.Color.blue())
        embed.add_field(name="Current Role", value=current_tier, inline=True)
        embed.add_field(name="Score", value=f"{score*100:.1f}%", inline=True)
        
        breakdown = []
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        for metric_name, cfg in metrics.items():
            if not cfg.get("enabled", True):
                continue
            max_val = cfg.get("max", 100)
            weight = cfg.get("weight", 0)
            if metric_name == "tenure_days":
                val = (datetime.utcnow() - (member.joined_at or datetime.utcnow())).days
            elif metric_name == "achievements":
                val = len(dm.get_guild_data(guild.id, f"achievements_{member.id}", []))
            else:
                val = udata.get(metric_name, 0)
            normalized = max(0, min(1, val / max_val)) if max_val > 0 else 0
            breakdown.append(f"• {metric_name}: {val}/{max_val} ({normalized*weight*100:.1f}%)")
        
        embed.add_field(name="Score Breakdown", value="\n".join(breakdown), inline=False)
        await message.reply(embed=embed)

    async def handle_leaderboard(self, message: discord.Message):
        guild = message.guild
        config = self._get_full_config(guild.id)
        metrics = config.get("metrics", self._default_metrics)
        
        scores = []
        for member in guild.members:
            if member.bot:
                continue
            score = self._compute_score(guild.id, member.id, member, metrics)
            scores.append((member, score))
        
        scores.sort(key=lambda x: x[1], reverse=True)
        top_10 = scores[:10]
        
        embed = discord.Embed(title="🏆 Staff Promotion Leaderboard", color=discord.Color.gold())
        for i, (member, score) in enumerate(top_10, 1):
            embed.add_field(name=f"#{i} {member.display_name}", value=f"Score: {score*100:.1f}%", inline=True)
        
        if not top_10:
            embed.add_field(name="No data", value="No staff members evaluated yet", inline=False)
        
        await message.reply(embed=embed)

    async def handle_config_view(self, message: discord.Message):
        guild = message.guild
        config = self._get_full_config(guild.id)
        
        embed = discord.Embed(title="⚙️ Staff Promo Configuration", color=discord.Color.orange())
        embed.add_field(name="Auto Promote", value=str(config.get("settings", {}).get("auto_promote", True)), inline=True)
        embed.add_field(name="Min Tenure", value=f"{config.get('settings', {}).get('min_tenure_hours', 72)} hours", inline=True)
        
        await message.reply(embed=embed)

    async def handle_command(self, message: discord.Message, args: list):
        if not args:
            await self.handle_status(message)
            return
        
        subcmd = args[0].lower()
        if subcmd == "status":
            await self.handle_status(message)
        elif subcmd == "leaderboard" or subcmd == "lb":
            await self.handle_leaderboard(message)
        elif subcmd == "config":
            await self.handle_config_view(message)
        else:
            await message.reply(f"Unknown subcommand: {subcmd}. Use `!staffpromo status`, `leaderboard`, or `config`")