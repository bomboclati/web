import discord
from data_manager import dm
import time
from enum import Enum

class AITier(Enum):
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class AIBilling:
    """
    AI Tier System - Free, Premium, Enterprise
    Tracks usage, manages subscriptions, handles upgrades.
    """
    
    TIER_CONFIG = {
        "free": {
            "name": "Free",
            "price": 0,
            "daily_limit": 50,
            "features": ["basic_chat", "memory"],
            "color": discord.Color.gray()
        },
        "premium": {
            "name": "Premium", 
            "price": 9.99,
            "daily_limit": 500,
            "features": ["basic_chat", "memory", "web_search", "image_gen"],
            "color": discord.Color.gold()
        },
        "enterprise": {
            "name": "Enterprise",
            "price": 49.99,
            "daily_limit": -1,  # Unlimited
            "features": ["basic_chat", "memory", "web_search", "image_gen", "custom_ai", "priority"],
            "color": discord.Color.blurple()
        }
    }
    
    def __init__(self, bot):
        self.bot = bot
    
    """Get user's tier"""
    def get_user_tier(self, guild_id: int, user_id: int) -> str:
        user_tiers = dm.get_guild_data(guild_id, "ai_user_tiers", {})
        return user_tiers.get(str(user_id), "free")
    
    def get_guild_tier(self, guild_id: int) -> str:
        return dm.get_guild_data(guild_id, "ai_guild_tier", "free")
    
    """Check usage limits"""
    def check_limit(self, guild_id: int, user_id: int) -> tuple:
        """Returns (can_use, remaining, limit)"""
        tier = self.get_user_tier(guild_id, user_id)
        config = self.TIER_CONFIG[tier]
        
        if config["daily_limit"] == -1:
            return True, -1, -1  # Unlimited
        
        # Get today's usage
        usage_key = f"ai_usage_{user_id}_{time.strftime('%Y%m%d')}"
        usage = dm.get_guild_data(guild_id, "ai_daily_usage", {}).get(usage_key, 0)
        
        limit = config["daily_limit"]
        remaining = limit - usage
        
        return remaining > 0, remaining, limit
    
    """Record usage"""
    def record_usage(self, guild_id: int, user_id: int, amount: int = 1):
        tier = self.get_user_tier(guild_id, user_id)
        config = self.TIER_CONFIG[tier]
        
        if config["daily_limit"] == -1:
            return  # Unlimited, no tracking
        
        usage_key = f"ai_usage_{user_id}_{time.strftime('%Y%m%d')}"
        usage_data = dm.get_guild_data(guild_id, "ai_daily_usage", {})
        usage_data[usage_key] = usage_data.get(usage_key, 0) + amount
        dm.update_guild_data(guild_id, "ai_daily_usage", usage_data)
    
    """Upgrade user tier"""
    def upgrade_user(self, guild_id: int, user_id: int, tier: str, period_days: int = 30):
        if tier not in self.TIER_CONFIG:
            return False
        
        user_tiers = dm.get_guild_data(guild_id, "ai_user_tiers", {})
        
        user_tiers[str(user_id)] = {
            "tier": tier,
            "upgraded_at": time.time(),
            "expires_at": time.time() + (period_days * 86400)
        }
        
        dm.update_guild_data(guild_id, "ai_user_tiers", user_tiers)
        return True
    
    """Check features"""
    def has_feature(self, guild_id: int, user_id: int, feature: str) -> bool:
        tier = self.get_user_tier(guild_id, user_id)
        config = self.TIER_CONFIG[tier]
        
        return feature in config["features"]
    
    """Show tier info"""
    def show_tier_info(self, guild_id: int, user_id: int) -> discord.Embed:
        tier = self.get_user_tier(guild_id, user_id)
        config = self.TIER_CONFIG[tier]
        
        can_use, remaining, limit = self.check_limit(guild_id, user_id)
        
        embed = discord.Embed(
            title=f"🤖 AI Tier: {config['name']}",
            color=config["color"]
        )
        
        if limit == -1:
            embed.add_field(name="Usage", value="∞ Unlimited", inline=True)
        else:
            embed.add_field(name="Remaining", value=f"{remaining}/{limit}", inline=True)
        
        features = "\n".join([f"✓ {f}" for f in config["features"]])
        embed.add_field(name="Features", value=features or "None", inline=False)
        
        if tier != "enterprise":
            embed.add_field(
                name="Upgrade", 
                value="/premium - $9.99/mo\n/enterprise - $49.99/mo",
                inline=False
            )
        
        return embed
    
    """Reset daily usage (cron job)"""
    def reset_daily_usage(self, guild_id: int):
        yesterday = time.strftime('%Y%m%d', time.localtime(time.time() - 86400))
        usage_key = f"ai_usage_*{yesterday}*"
        
        usage_data = dm.get_guild_data(guild_id, "ai_daily_usage", {})
        
        # Remove old entries
        keys_to_remove = [k for k in usage_data.keys() if yesterday in k]
        for k in keys_to_remove:
            del usage_data[k]
        
        dm.update_guild_data(guild_id, "ai_daily_usage", usage_data)
    
    """Check expired subscriptions"""
    def check_expired(self, guild_id: int):
        user_tiers = dm.get_guild_data(guild_id, "ai_user_tiers", {})
        
        for user_id, tier_data in user_tiers.items():
            if isinstance(tier_data, dict):
                if tier_data.get("expires_at", 0) < time.time():
                    user_tiers[user_id] = "free"  # Downgrade
        
        dm.update_guild_data(guild_id, "ai_user_tiers", user_tiers)