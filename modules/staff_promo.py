import asyncio
from datetime import datetime
import json
import discord
from discord.ext import commands, tasks
from typing import Optional

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
        
        self._default_trial_settings = {
            "enabled": True,
            "duration_days": 14,
            "evaluation_metrics": {
                "activity_score_min": 0.3,
                "ticket_resolution_min": 5,
                "voice_hours_min": 10
            },
            "auto_revert_on_fail": True
        }
        
        self._default_metrics = {
            "xp": {"weight": 0.20, "max": 5000, "enabled": True},
            "tenure_days": {"weight": 0.15, "max": 90, "enabled": True},
            "messages": {"weight": 0.15, "max": 1000, "enabled": True},
            "achievements": {"weight": 0.10, "max": 20, "enabled": True},
            "voice_minutes": {"weight": 0.10, "max": 3600, "enabled": True},
            "rep_received": {"weight": 0.08, "max": 100, "enabled": True},
            "rep_given": {"weight": 0.07, "max": 100, "enabled": True},
            "gamification_score": {"weight": 0.15, "max": 100, "enabled": True},  # New metric for gamification
            "badge_count": {"weight": 0.05, "max": 10, "enabled": True},      # New metric for badges
            "level": {"weight": 0.02, "max": 50, "enabled": True}            # New metric for level
        }
        
        self._default_settings = {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        }
        
        self._default_tier_requirements = {
            "Trial Moderator": {},
            "Moderator": {"messages": 200, "achievements": 3},
            "Senior Moderator": {"messages": 500, "achievements": 8, "tenure_days": 30},
            "Head Moderator": {"messages": 1000, "achievements": 15, "tenure_days": 60},
            "Admin": {"messages": 2000, "achievements": 25, "tenure_days": 90},
        }
        
        self._default_achievement_bonuses = {
            "Helper": 1.2,
            "Event Organizer": 1.15,
            "Problem Solver": 1.1,
            "Active Contributor": 1.1,
            "Trusted": 1.05,
        }
        
        self._default_rewards = {
            "promotion_reward_coins": 500,
            "promotion_reward_title": True,
            "demotion_penalty_coins": 200,
        }
        
        self._last_promotion_time = {}
        self._last_demotion_time = {}
        self._last_notification_time = {}
        # Start the promotion loop only if the bot is ready
        # This will be handled in the loop itself

    def _get_full_config(self, guild_id: int) -> dict:
        cfg = dm.get_guild_data(guild_id, "staff_promo_config", {})
        cfg.setdefault("tiers", self._default_tiers)
        cfg.setdefault("metrics", self._default_metrics)
        cfg.setdefault("settings", self._default_settings)
        cfg.setdefault("rewards", self._default_rewards)
        cfg.setdefault("roles_by_tier", {})
        cfg.setdefault("tier_requirements", self._default_tier_requirements)
        cfg.setdefault("achievement_bonuses", self._default_achievement_bonuses)
        cfg.setdefault("pending_reviews", [])
        cfg.setdefault("trial_settings", self._default_trial_settings)
        cfg.setdefault("staff_applications", {})
        cfg.setdefault("application_tracking", {})
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
        
        role_ids = dict(config.get("roles_by_tier", {}))
        for tier in tiers:
            tier_name = tier.get("name")
            if tier_name not in role_ids or not role_ids[tier_name]:
                role_name = tier.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name] = r.id
        
        for member in guild.members:
            if member.bot or member.id in excluded:
                continue
            
            if not self._check_tenure(member, settings):
                continue
            
            await self._evaluate_member(guild, member, tiers, role_ids, metrics, settings, config)

    def _check_tenure(self, member: discord.Member, settings: dict) -> bool:
        min_hours = settings.get("min_tenure_hours", 72)
        if not member.joined_at:
            return False
        tenure_hours = (datetime.utcnow() - member.joined_at).total_seconds() / 3600
        return tenure_hours >= min_hours

    async def _check_trial_period(self, guild_id: int, member: discord.Member, config: dict) -> Optional[str]:
        """Check trial period status: 'active', 'complete', 'revert', 'extend', or None if not in trial"""
        trial_settings = config.get("trial_settings", self._default_trial_settings)
        if not trial_settings.get("enabled", True):
            return None
            
        # Check if member has trial moderator role
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        trial_role_id = None
        
        for tier in tiers:
            if tier.get("name") == "Trial Moderator":
                trial_role_id = role_ids.get(tier.get("name"))
                break
                
        if not trial_role_id:
            return None
            
        has_trial_role = any(r.id == trial_role_id for r in member.roles)
        if not has_trial_role:
            return None
            
        # Get trial start time from user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        trial_start = udata.get("trial_start_time")
        
        if not trial_start:
            # Set trial start time if not set
            udata["trial_start_time"] = datetime.utcnow().timestamp()
            dm.update_guild_data(guild_id, f"user_{member.id}", udata)
            return "active"
            
        trial_duration_days = trial_settings.get("duration_days", 14)
        trial_seconds = trial_duration_days * 24 * 3600
        elapsed_time = datetime.utcnow().timestamp() - trial_start
        
        if elapsed_time >= trial_seconds:
            # Trial period ended, evaluate performance
            evaluation_result = await self._evaluate_trial_performance(guild_id, member, trial_settings, config)
            if evaluation_result == "pass":
                return "complete"
            elif evaluation_result == "fail":
                return "revert"
            else:
                return "extend"  # Need more time
        else:
            return "active"

    async def _evaluate_trial_performance(self, guild_id: int, member: discord.Member, trial_settings: dict, config: dict) -> str:
        """Evaluate trial performance based on metrics"""
        metrics = trial_settings.get("evaluation_metrics", {
            "activity_score_min": 0.3,
            "ticket_resolution_min": 5,
            "voice_hours_min": 10
        })
        
        # Get user data
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        
        # Check activity score (from promotion system)
        current_score = self._compute_score(guild_id, member.id, member, config.get("metrics", self._default_metrics))
        activity_score_min = metrics.get("activity_score_min", 0.3)
        
        # Check ticket resolutions (would need ticket system integration)
        ticket_resolutions = udata.get("ticket_resolutions", 0)
        ticket_resolution_min = metrics.get("ticket_resolution_min", 5)
        
        # Check voice hours
        voice_hours = udata.get("voice_minutes", 0) / 60  # Convert to hours
        voice_hours_min = metrics.get("voice_hours_min", 10)
        
        # Evaluate criteria
        score_pass = current_score >= activity_score_min
        ticket_pass = ticket_resolutions >= ticket_resolution_min
        voice_pass = voice_hours >= voice_hours_min
        
        # Require at least 2 out of 3 criteria to pass
        passes = sum([score_pass, ticket_pass, voice_pass])
        
        if passes >= 2:
            return "pass"
        else:
            return "fail"

    async def _handle_trial_revert(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, settings, config):
        """Handle automatic reversion from trial period"""
        # Remove trial moderator role
        trial_role_id = role_ids.get("Trial Moderator")
        if trial_role_id:
            trial_role = guild.get_role(trial_role_id)
            if trial_role and trial_role in member.roles:
                try:
                    await member.remove_roles(trial_role)
                except Exception as e:
                    logger.error(f"Failed to remove trial role: {e}")
        
        # Add back any previous roles if they existed
        # For simplicity, we'll just remove the trial role and let system evaluate normally
        
        # Reset trial data
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        if "trial_start_time" in udata:
            del udata["trial_start_time"]
            dm.update_guild_data(guild.id, f"user_{member.id}", udata)
        
        # Notify user
        try:
            await member.send("📋 Your trial period has ended and you did not meet the requirements for promotion. "
                            "Your Trial Moderator role has been removed. You can reapply after improving your activity.")
        except:
            pass
            
        logger.info(f"StaffPromo[{guild.id}] {member} reverted from trial period due to insufficient performance")

    async def _evaluate_member(self, guild: discord.Guild, member: discord.Member, tiers, role_ids, metrics, settings, config):
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
        
        # Check for trial period completion/reversion
        trial_status = await self._check_trial_period(guild.id, member, config)
        if trial_status == "revert":
            await self._handle_trial_revert(guild, member, tiers, role_ids, settings, config)
            return
        elif trial_status == "extend":
            # Extend trial period, don't process promotion
            return
        elif trial_status == "complete":
            # Trial completed successfully, allow promotion to next tier
            pass
        
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        target_index = -1 if not target_tier else tiers.index(target_tier)
        
        if target_index > current_index:
            target_tier_name = target_tier.get("name")
            if not self._check_tier_requirements(guild.id, member, target_tier_name, config):
                return
            
            if settings.get("review_mode", False):
                await self._submit_promotion_review(guild, member, target_tier, score, config)
                return
            
            await self._promote_member(guild, member, target_tier, tiers, role_ids, current_index, settings, config)
            self._last_promotion_time[cooldown_key] = datetime.utcnow()
        elif settings.get("auto_demote", False) and target_index < current_index and current_index > 0:
            demotion_cooldown_key = f"{guild.id}_{user_id}_demote"
            demotion_cooldown_hours = settings.get("demotion_cooldown_hours", 168)
            if demotion_cooldown_key in self._last_demotion_time:
                last = self._last_demotion_time[demotion_cooldown_key]
                if (datetime.utcnow() - last).total_seconds() < demotion_cooldown_hours * 3600:
                    return
            
            buffer = settings.get("demotion_threshold_buffer", 0.1)
            if score < target_tier.get("threshold", 0) - buffer:
                await self._demote_member(guild, member, target_index, tiers, role_ids, current_index, settings, config)
                self._last_demotion_time[demotion_cooldown_key] = datetime.utcnow()
        
        if settings.get("notify_near_promotion", True):
            await self._check_progress_notification(guild, member, score, tiers, role_ids, settings)

    def _get_current_tier_index(self, member: discord.Member, tiers, role_ids) -> int:
        for idx, tier in enumerate(tiers):
            rid = role_ids.get(tier.get("name"))
            if rid and any(r.id == rid for r in member.roles):
                return idx
        return -1

    def _calculate_gamification_score(self, guild_id: int, user_id: int) -> int:
        """Calculate a gamification score based on badges, quests, and skills"""
        try:
            # Import gamification system to access its data
            from modules.gamification import AdaptiveGamification
            # Note: In a real implementation, we'd need access to the bot instance
            # For now, we'll calculate based on available data
            
            # Base score from badges
            badges = dm.get_guild_data(guild_id, f"badges_{user_id}", [])
            badge_score = len(badges) * 10  # 10 points per badge
            
            # Bonus for rare/evolved badges
            evolved_bonus = 0
            for badge_data in badges:
                evolved_level = badge_data.get("evolved_level", 1)
                if evolved_level > 1:
                    evolved_bonus += (evolved_level - 1) * 5
            
            # Skill points (if available)
            skills = dm.get_guild_data(guild_id, f"skills_{user_id}", {})
            skill_score = 0
            for skill_name, skill_data in skills.items():
                level = skill_data.get("level", 1)
                skill_score += level * 2  # 2 points per skill level
            
            # Quest completion bonus
            quests_completed = dm.get_guild_data(guild_id, f"user_{user_id}", {}).get("quests_completed", 0)
            quest_bonus = quests_completed * 5  # 5 points per completed quest
            
            total_score = badge_score + evolved_bonus + skill_score + quest_bonus
            return min(100, total_score)  # Cap at 100
        except Exception as e:
            logger.error(f"Error calculating gamification score: {e}")
            return 0

    def _get_user_level(self, guild_id: int, user_id: int) -> int:
        """Get user level from leveling system or calculate from XP"""
        try:
            # Try to get from leveling system first
            level_data = dm.get_guild_data(guild_id, f"level_{user_id}", {})
            if level_data:
                return level_data.get("level", 1)
            
            # Fallback: calculate from XP
            user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
            xp = user_data.get("xp", 0)
            # Simple level calculation: every 1000 XP = 1 level
            level = max(1, xp // 1000)
            return min(50, level)  # Cap at 50
        except Exception as e:
            logger.error(f"Error getting user level: {e}")
            return 1

    def _check_tier_requirements(self, guild_id: int, member: discord.Member, tier_name: str, config: dict) -> bool:
        requirements = config.get("tier_requirements", self._default_tier_requirements)
        tier_reqs = requirements.get(tier_name, {})
        
        if not tier_reqs:
            return True
        
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        joined_at = member.joined_at or datetime.utcnow()
        tenure_days = (datetime.utcnow() - joined_at).days
        user_achievements = dm.get_guild_data(guild_id, f"achievements_{member.id}", [])
        
        missing = []
        for req_type, req_value in tier_reqs.items():
            if req_type == "messages":
                if udata.get("total_messages", 0) < req_value:
                    missing.append(f"messages: {udata.get('total_messages', 0)}/{req_value}")
            elif req_type == "achievements":
                if len(user_achievements) < req_value:
                    missing.append(f"achievements: {len(user_achievements)}/{req_value}")
            elif req_type == "tenure_days":
                if tenure_days < req_value:
                    missing.append(f"tenure: {tenure_days}/{req_value} days")
            elif req_type == "xp":
                if udata.get("xp", 0) < req_value:
                    missing.append(f"XP: {udata.get('xp', 0)}/{req_value}")
        
        if missing:
            logger.info(f"StaffPromo[{guild_id}] {member} missing requirements for {tier_name}: {', '.join(missing)}")
            return False
        
        return True

    async def _submit_promotion_review(self, guild: discord.Guild, member: discord.Member, target_tier, score: float, config: dict):
        pending = config.get("pending_reviews", [])
        
        for review in pending:
            if review.get("user_id") == member.id and review.get("tier_name") == target_tier.get("name"):
                return
        
        review_data = {
            "user_id": member.id,
            "user_name": str(member),
            "tier_name": target_tier.get("name"),
            "score": score,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        pending.append(review_data)
        config["pending_reviews"] = pending
        dm.update_guild_data(guild.id, "staff_promo_config", config)
        
        review_ch_id = config.get("settings", {}).get("review_channel")
        if review_ch_id:
            channel = guild.get_channel(int(review_ch_id))
            if channel:
                embed = discord.Embed(
                    title="📋 Promotion Review Request",
                    description=f"{member.mention} is eligible for promotion to **{target_tier.get('name')}**",
                    color=discord.Color.yellow()
                )
                embed.add_field(name="Score", value=f"{score*100:.1f}%", inline=True)
                embed.add_field(name="Member", value=member.mention, inline=True)
                embed.add_field(name="Actions", value="`!staffpromo approve @user` or `!staffpromo reject @user`", inline=False)
                await channel.send(embed=embed)
        
        try:
            await member.send(f"📋 Your promotion to **{target_tier.get('name')}** is pending review.")
        except:
            pass

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
            "gamification_score": self._calculate_gamification_score(guild_id, user_id),
            "badge_count": len(dm.get_guild_data(guild_id, f"badges_{user_id}", [])),
            "level": self._get_user_level(guild_id, user_id)
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
        
        config = self._get_full_config(guild_id)
        bonuses = config.get("achievement_bonuses", self._default_achievement_bonuses)
        
        user_achievements = dm.get_guild_data(guild_id, f"achievements_{user_id}", [])
        total_bonus = 1.0
        for ach_name, multiplier in bonuses.items():
            if ach_name in user_achievements:
                total_bonus += (multiplier - 1.0)
        
        score = score * min(total_bonus, 2.0)
        
        return min(1.0, score)

    async def _promote_member(self, guild: discord.Guild, member: discord.Member, target_tier, tiers, role_ids, current_index, settings, config):
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
        
        await self._apply_promotion_rewards(guild, member, target_tier.get("name"), config)
        await self._log_promotion(guild, member, target_tier.get("name"), settings)

    async def _demote_member(self, guild: discord.Guild, member: discord.Member, target_index, tiers, role_ids, current_index, settings, config):
        if target_index < 0:
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
            new_tier_name = "None"
        else:
            target_tier = tiers[target_index]
            new_role_id = role_ids.get(target_tier.get("name"))
            
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
            
            if new_role_id:
                try:
                    role = guild.get_role(new_role_id)
                    if role:
                        await member.add_roles(role)
                except:
                    pass
            
            new_tier_name = target_tier.get("name")
        
        await self._apply_demotion_penalty(guild, member, new_tier_name, config)
        await self._log_demotion(guild, member, new_tier_name, settings)

    async def _apply_promotion_rewards(self, guild: discord.Guild, member: discord.Member, new_tier: str, config: dict):
        rewards = config.get("rewards", self._default_rewards)
        
        coins = rewards.get("promotion_reward_coins", 0)
        if coins > 0:
            try:
                user_data = dm.get_guild_data(guild.id, f"user_{member.id}", {})
                user_data["coins"] = user_data.get("coins", 0) + coins
                dm.update_guild_data(guild.id, f"user_{member.id}", user_data)
            except Exception as e:
                logger.error(f"Failed to give promotion coins: {e}")
        
        if rewards.get("promotion_reward_title", True):
            try:
                title_data = dm.get_guild_data(guild.id, f"user_{member.id}_titles", {})
                title = f"Promoted {new_tier}"
                if title not in title_data.get("titles", []):
                    title_data.setdefault("titles", []).append(title)
                    dm.update_guild_data(guild.id, f"user_{member.id}_titles", title_data)
            except:
                pass

    async def _apply_demotion_penalty(self, guild: discord.Guild, member: discord.Member, new_tier: str, config: dict):
        rewards = config.get("rewards", self._default_rewards)
        
        coins = rewards.get("demotion_penalty_coins", 0)
        if coins > 0:
            try:
                user_data = dm.get_guild_data(guild.id, f"user_{member.id}", {})
                user_data["coins"] = max(0, user_data.get("coins", 0) - coins)
                dm.update_guild_data(guild.id, f"user_{member.id}", user_data)
            except Exception as e:
                logger.error(f"Failed to apply demotion penalty: {e}")

    async def _check_progress_notification(self, guild: discord.Guild, member: discord.Member, score: float, tiers, role_ids, settings):
        notif_key = f"{guild.id}_{member.id}_progress"
        if notif_key in self._last_notification_time:
            last = self._last_notification_time[notif_key]
            if (datetime.utcnow() - last).total_seconds() < 86400:
                return
        
        config = self._get_full_config(guild.id)
        current_index = self._get_current_tier_index(member, tiers, role_ids)
        
        if current_index < len(tiers) - 1:
            next_tier = tiers[current_index + 1]
            threshold = next_tier.get("threshold", 0)
            threshold_val = settings.get("near_promotion_threshold", 0.05)
            
            if threshold - score <= threshold_val and threshold - score > 0:
                percent_away = (threshold - score) * 100
                try:
                    await member.send(f"🎯 You're **{percent_away:.1f}%** away from being promoted to **{next_tier.get('name')}**! Keep it up!")
                    self._last_notification_time[notif_key] = datetime.utcnow()
                except:
                    pass

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

    async def _log_demotion(self, guild: discord.Guild, member: discord.Member, new_tier: str, settings: dict):
        logger.info(f"StaffPromo[{guild.id}] {member} demoted to {new_tier}")
        
        log_ch_id = settings.get("log_channel")
        if log_ch_id:
            channel = guild.get_channel(int(log_ch_id))
            if channel:
                try:
                    embed = discord.Embed(
                        title="📉 Staff Demotion",
                        description=f"{member.mention} has been demoted to **{new_tier}**",
                        color=discord.Color.red()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                except:
                    pass
        
        announce_ch_id = settings.get("announce_channel")
        if announce_ch_id and settings.get("notify_on_demotion", True):
            channel = guild.get_channel(int(announce_ch_id))
            if channel:
                try:
                    await channel.send(f"⚠️ {member.mention} has been demoted to **{new_tier}**.")
                except:
                    pass

    async def manual_promote(self, guild: discord.Guild, target_member: discord.Member, tier_name: str, config: dict):
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
        if not tier:
            return False, "Tier not found"
        
        tier_index = tiers.index(tier)
        
        for t in tiers:
            tier_name_key = t.get("name")
            if tier_name_key not in role_ids or not role_ids[tier_name_key]:
                role_name = t.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name_key] = r.id
        
        current_index = self._get_current_tier_index(target_member, tiers, role_ids)
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            t = tiers[idx]
            rid = role_ids.get(t.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in member.roles:
                    try:
                        await target_member.remove_roles(rm)
                    except:
                        pass
        
        new_role_id = role_ids.get(tier.get("name"))
        if new_role_id:
            try:
                role = guild.get_role(new_role_id)
                if role:
                    await target_member.add_roles(role)
            except Exception as e:
                return False, str(e)
        
        await self._apply_promotion_rewards(guild, target_member, tier.get("name"), config)
        
        cooldown_key = f"{guild.id}_{target_member.id}"
        self._last_promotion_time[cooldown_key] = datetime.utcnow()
        
        return True, f"Promoted to {tier.get('name')}"

    async def manual_demote(self, guild: discord.Guild, target_member: discord.Member, tier_name: str, config: dict):
        tiers = config.get("tiers", self._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        if tier_name.lower() == "none":
            tier_index = -1
        else:
            tier = next((t for t in tiers if t.get("name", "").lower() == tier_name.lower()), None)
            if not tier:
                return False, "Tier not found"
            tier_index = tiers.index(tier)
        
        for t in tiers:
            tier_name_key = t.get("name")
            if tier_name_key not in role_ids or not role_ids[tier_name_key]:
                role_name = t.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name_key] = r.id
        
        current_index = self._get_current_tier_index(target_member, tiers, role_ids)
        
        for idx in range(current_index + 1):
            if idx >= len(tiers):
                continue
            t = tiers[idx]
            rid = role_ids.get(t.get("name"))
            if rid:
                rm = guild.get_role(rid)
                if rm and rm in target_member.roles:
                    try:
                        await target_member.remove_roles(rm)
                    except:
                        pass
        
        if tier_index >= 0:
            target_tier = tiers[tier_index]
            new_role_id = role_ids.get(target_tier.get("name"))
            if new_role_id:
                try:
                    role = guild.get_role(new_role_id)
                    if role:
                        await target_member.add_roles(role)
                except:
                    pass
            new_tier_name = target_tier.get("name")
        else:
            new_tier_name = "None"
        
        await self._apply_demotion_penalty(guild, target_member, new_tier_name, config)
        
        demotion_cooldown_key = f"{guild.id}_{target_member.id}_demote"
        self._last_demotion_time[demotion_cooldown_key] = datetime.utcnow()
        
        return True, f"Demoted to {new_tier_name}"

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
        settings = config.get("settings", self._default_settings)
        rewards = config.get("rewards", self._default_rewards)
        
        embed = discord.Embed(
            title="🧭 Staff Auto-Promotion System",
            description="Automatically promotes/demotes staff based on performance metrics",
            color=discord.Color.green()
        )
        
        tiers_text = "\n".join([f"• **{t['name']}**: {int(t['threshold']*100)}%" for t in tiers])
        embed.add_field(name="📊 Promotion Tiers", value=tiers_text or "No tiers configured", inline=False)
        
        embed.add_field(
            name="⚙️ Configuration",
            value=(
                f"• Auto-promote: `{settings.get('auto_promote', True)}`\n"
                f"• Auto-demote: `{settings.get('auto_demote', False)}`\n"
                f"• Min tenure: `{settings.get('min_tenure_hours', 72)}h`\n"
                f"• Promotion cooldown: `{settings.get('promotion_cooldown_hours', 24)}h`"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🎁 Promotion Rewards",
            value=(
                f"• Coins: `{rewards.get('promotion_reward_coins', 500)}`\n"
                f"• Title: `{rewards.get('promotion_reward_title', True)}`\n"
                f"• Demotion penalty: `{rewards.get('demotion_penalty_coins', 200)}` coins"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💬 Commands",
            value=(
                "• `!staffpromo status` - Check your score\n"
                "• `!staffpromo leaderboard` - Top staff\n"
                "• `!staffpromo progress` - Progress to next tier\n"
                "• `!staffpromo config` - View config (admin)\n"
                "• `!staffpromo promote @user <tier>` - Promote (admin)\n"
                "• `!staffpromo demote @user <tier>` - Demote (admin)\n"
                "• `!staffpromo exclude add/remove @user` - Exclude user\n"
                "• `!staffpromo roles add/remove <tier> @role` - Map roles"
            ),
            inline=False
        )
        
        await doc_channel.send(embed=embed)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["staffpromo status"] = json.dumps({"command_type": "staffpromo_status"})
        custom_cmds["staffpromo leaderboard"] = json.dumps({"command_type": "staffpromo_leaderboard"})
        custom_cmds["staffpromo config"] = json.dumps({"command_type": "staffpromo_config"})
        custom_cmds["staffpromo progress"] = json.dumps({"command_type": "staffpromo_progress"})
        custom_cmds["staffpromo promote"] = json.dumps({"command_type": "staffpromo_promote"})
        custom_cmds["staffpromo demote"] = json.dumps({"command_type": "staffpromo_demote"})
        custom_cmds["staffpromo exclude"] = json.dumps({"command_type": "staffpromo_exclude"})
        custom_cmds["staffpromo roles"] = json.dumps({"command_type": "staffpromo_roles"})
        custom_cmds["staffpromo review"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo requirements"] = json.dumps({"command_type": "staffpromo_requirements"})
        custom_cmds["staffpromo bonuses"] = json.dumps({"command_type": "staffpromo_bonuses"})
        custom_cmds["staffpromo approve"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo reject"] = json.dumps({"command_type": "staffpromo_review"})
        custom_cmds["staffpromo tiers"] = json.dumps({"command_type": "staffpromo_tiers"})
        custom_cmds["tiers"] = json.dumps({"command_type": "staffpromo_tiers"})
        
        custom_cmds["help staffpromo"] = json.dumps({
            "command_type": "help_embed",
            "title": "Staff Promotion System Help",
            "description": "Auto-promotes/demotes staff based on performance metrics.",
            "fields": [
                {"name": "!staffpromo status", "value": "Check your current promotion score.", "inline": False},
                {"name": "!staffpromo leaderboard", "value": "View top staff members by score.", "inline": False},
                {"name": "!staffpromo progress", "value": "See progress to next tier.", "inline": False},
                {"name": "!staffpromo requirements", "value": "View tier requirements.", "inline": False},
                {"name": "!staffpromo bonuses", "value": "View achievement score bonuses.", "inline": False},
                {"name": "!staffpromo config", "value": "View configuration (admin).", "inline": False},
                {"name": "!staffpromo promote @user <tier>", "value": "Manually promote user (admin).", "inline": False},
                {"name": "!staffpromo demote @user <tier>", "value": "Manually demote user (admin).", "inline": False},
                {"name": "!staffpromo exclude add/remove @user", "value": "Exclude from auto-promotion (admin).", "inline": False},
                {"name": "!staffpromo roles add/remove <tier> @role", "value": "Map roles to tiers (admin).", "inline": False},
                {"name": "!staffpromo tiers", "value": "Manage promotion tiers interactively (admin).", "inline": False},
                {"name": "!tiers", "value": "Manage promotion tiers interactively (admin).", "inline": False},
                {"name": "!staffpromo review", "value": "View pending reviews (admin).", "inline": False},
                {"name": "!staffpromo approve @user", "value": "Approve promotion (admin).", "inline": False},
                {"name": "!staffpromo reject @user", "value": "Reject promotion (admin).", "inline": False},
                {"name": "!help staffpromo", "value": "Show this help embed.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        await interaction.followup.send("✅ Staff Promotion System set up!", ephemeral=True)