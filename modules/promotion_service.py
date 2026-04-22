import asyncio
from datetime import datetime
import discord
from typing import Optional, List, Dict, Any
from data_manager import dm
from logger import logger


class PromotionService:
    """Service class encapsulating staff promotion logic"""
    
    def __init__(self):
        self._default_metrics = {
            "xp": {"weight": 0.15, "max": 5000, "enabled": True},
            "tenure_days": {"weight": 0.10, "max": 90, "enabled": True},
            "messages": {"weight": 0.10, "max": 1000, "enabled": True},
            "tickets_resolved": {"weight": 0.15, "max": 50, "enabled": True},
            "achievements": {"weight": 0.10, "max": 20, "enabled": True},
            "voice_minutes": {"weight": 0.08, "max": 3600, "enabled": True},
            "rep_received": {"weight": 0.08, "max": 100, "enabled": True},
            "rep_given": {"weight": 0.06, "max": 100, "enabled": True},
            "gamification_score": {"weight": 0.10, "max": 100, "enabled": True},
            "badge_count": {"weight": 0.05, "max": 10, "enabled": True},
            "level": {"weight": 0.03, "max": 50, "enabled": True}
        }
        
        self._default_achievement_bonuses = {
            "Helper": 1.2,
            "Event Organizer": 1.15,
            "Problem Solver": 1.1,
            "Active Contributor": 1.1,
            "Trusted": 1.05,
        }
        
        self._default_tier_requirements = {}
        
        self._last_promotion_time = {}
        self._last_demotion_time = {}
        self._last_notification_time = {}

    def _calculate_gamification_score(self, guild_id: int, user_id: int) -> int:
        """Calculate a gamification score based on badges, quests, and skills."""
        try:
            # Base score from badges earned
            awarded = dm.load_json("awarded_badges", default={}).get(str(guild_id), {}).get(str(user_id), [])
            badge_score = len(awarded) * 5
            
            # Bonus from legacy badge data if it exists
            legacy_badges = dm.get_guild_data(guild_id, f"badges_{user_id}", [])
            if isinstance(legacy_badges, list):
                for b in legacy_badges:
                    if isinstance(b, dict):
                        badge_score += b.get("evolved_level", 1) * 2
            
            # Quest completion bonus
            udata = dm.get_guild_data(guild_id, f"user_{user_id}", {})
            quests = udata.get("quests_completed", 0)
            
            # Skill points
            skills = dm.get_guild_data(guild_id, f"skills_{user_id}", {})
            skill_score = sum(s.get("level", 1) for s in skills.values()) if isinstance(skills, dict) else 0
            
            total_score = badge_score + (quests * 5) + (skill_score * 2)
            return min(100, total_score)
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

    def _compute_score(self, guild_id: int, user_id: int, member: discord.Member, metrics: dict, config: Optional[dict] = None) -> float:
        """Compute promotion score based on various metrics"""
        now = discord.utils.utcnow()
        joined = member.joined_at or now
        tenure_days = (now - joined).days
        
        udata = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        values = {
            "xp": udata.get("xp", 0),
            "tenure_days": tenure_days,
            "messages": udata.get("total_messages", 0),
            "tickets_resolved": dm.get_guild_data(guild_id, f"tickets_resolved_{user_id}", 0),
            "achievements": len(dm.get_guild_data(guild_id, f"achievements_{user_id}", [])),
            "voice_minutes": udata.get("voice_minutes", 0),
            "rep_received": udata.get("rep_received", 0),
            "rep_given": udata.get("rep_given", 0),
            "gamification_score": self._calculate_gamification_score(guild_id, user_id),
            "badge_count": len(dm.get_guild_data(guild_id, f"badges_{user_id}", [])),
            "level": self._get_user_level(guild_id, user_id)
        }
        
        score = 0.0
        for metric_name, m_config in metrics.items():
            if not m_config.get("enabled", True):
                continue
            weight = m_config.get("weight", 0)
            max_val = m_config.get("max", 100)
            raw_val = values.get(metric_name, 0)
            normalized = max(0, min(1, raw_val / max_val)) if max_val > 0 else 0
            score += normalized * weight
        
        # Apply achievement bonuses
        bonuses = config.get("achievement_bonuses", self._default_achievement_bonuses) if config else self._default_achievement_bonuses
        
        user_achievements = dm.get_guild_data(guild_id, f"achievements_{user_id}", [])
        # Also check new awarded_badges.json
        awarded = dm.load_json("awarded_badges", default={}).get(str(guild_id), {}).get(str(user_id), [])

        total_bonus = 1.0
        for ach_name, multiplier in bonuses.items():
            # Check by name in legacy data or by ID in new data
            has_ach = any(a.get("name") == ach_name for a in user_achievements if isinstance(a, dict)) or ach_name in awarded
            if has_ach:
                total_bonus += (multiplier - 1.0)
        
        score = score * min(total_bonus, 2.0)
        
        return min(1.0, score)

    async def _submit_promotion_review(self, guild: discord.Guild, member: discord.Member, target_tier, score: float, config: dict):
        """Submit a promotion for review"""
        pending = config.get("pending_reviews", [])
        
        for review in pending:
            if review.get("user_id") == member.id and review.get("tier_name") == target_tier.get("name"):
                return
        
        review_data = {
            "user_id": member.id,
            "user_name": str(member),
            "tier_name": target_tier.get("name"),
            "score": score,
            "timestamp": discord.utils.utcnow().isoformat(),
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

    def _check_tenure(self, member: discord.Member, settings: dict) -> bool:
        """Check if member meets minimum tenure requirements"""
        min_hours = settings.get("min_tenure_hours", 72)
        if not member.joined_at:
            return False
        tenure_hours = (discord.utils.utcnow() - member.joined_at).total_seconds() / 3600
        return tenure_hours >= min_hours

    def _check_tier_requirements(self, guild_id: int, member: discord.Member, tier_name: str, config: dict) -> bool:
        """Check if member meets specific tier requirements"""
        requirements = config.get("tier_requirements", self._default_tier_requirements)
        tier_reqs = requirements.get(tier_name, {})
        
        if not tier_reqs:
            return True
        
        udata = dm.get_guild_data(guild_id, f"user_{member.id}", {})
        joined_at = member.joined_at or discord.utils.utcnow()
        tenure_days = (discord.utils.utcnow() - joined_at).days
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