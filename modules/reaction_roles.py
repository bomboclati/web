import discord
from discord.ext import commands
import json
import time
from typing import Dict, List, Optional
from data_manager import dm
from logger import logger

class ReactionRolesSystem:
    def __init__(self, bot):
        self.bot = bot

    def get_guild_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "reaction_roles", {
            "bindings": [], # list of {message_id, emoji, role_id, restrictions}
            "role_limit": 0,
            "log": []
        })

    def save_guild_config(self, guild_id: int, config: dict):
        dm.update_guild_data(guild_id, "reaction_roles", config)

    async def handle_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id: return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return

        member = payload.member
        if not member: return

        config = self.get_guild_config(guild.id)
        emoji_str = str(payload.emoji)

        # Find binding
        binding = None
        for b in config["bindings"]:
            if b["message_id"] == payload.message_id and b["emoji"] == emoji_str:
                binding = b
                break

        if not binding: return

        role = guild.get_role(binding["role_id"])
        if not role: return

        # Check Restrictions
        restrictions = binding.get("restrictions", {})

        # Min Account Age
        min_age = restrictions.get("min_account_age_days")
        if min_age:
            import datetime
            age = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
            if age < min_age: return

        # Min Level
        min_lvl = restrictions.get("min_level")
        if min_lvl:
            lvl = dm.get_guild_data(guild.id, f"user_{member.id}", {}).get("level", 1)
            if lvl < min_lvl: return

        # Prerequisite Role
        prereq = restrictions.get("prerequisite_role")
        if prereq and not any(r.id == prereq for r in member.roles):
            return

        # Incompatible Role
        incomp = restrictions.get("incompatible_role")
        if incomp and any(r.id == incomp for r in member.roles):
            return

        # Role Limit
        limit = config.get("role_limit", 0)
        if limit > 0:
            rr_roles = [b["role_id"] for b in config["bindings"]]
            member_rr_count = sum(1 for r in member.roles if r.id in rr_roles)
            if member_rr_count >= limit:
                return # Over limit

        try:
            await member.add_roles(role, reason="Reaction Role Assignment")
            self._log_action(guild.id, member, "add", role)
        except Exception as e:
            logger.error(f"RR Error adding role: {e}")

    async def handle_reaction_remove(self, payload: discord.RawReactionActionEvent):
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return

        member = guild.get_member(payload.user_id)
        if not member or member.bot: return

        config = self.get_guild_config(guild.id)
        emoji_str = str(payload.emoji)

        binding = None
        for b in config["bindings"]:
            if b["message_id"] == payload.message_id and b["emoji"] == emoji_str:
                binding = b
                break

        if not binding: return

        role = guild.get_role(binding["role_id"])
        if not role: return

        try:
            await member.remove_roles(role, reason="Reaction Role Removal")
            self._log_action(guild.id, member, "remove", role)
        except Exception as e:
            logger.error(f"RR Error removing role: {e}")

    def _log_action(self, guild_id: int, member: discord.Member, action: str, role: discord.Role):
        config = self.get_guild_config(guild_id)
        log = config.get("log", [])
        log.append({
            "user": str(member),
            "user_id": member.id,
            "action": action,
            "role": role.name,
            "role_id": role.id,
            "ts": time.time()
        })
        config["log"] = log[-30:] # Keep last 30
        self.save_guild_config(guild_id, config)

    async def setup(self, interaction: discord.Interaction, params: dict = None):
        # AutoSetup integration
        guild_id = interaction.guild_id
        config = self.get_guild_config(guild_id)
        self.save_guild_config(guild_id, config)

        await interaction.followup.send("🎭 Reaction Roles system initialized. Use !reactionrolespanel to configure bindings.")
        return True
