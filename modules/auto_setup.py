import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from data_manager import dm
from logger import logger


class SetupState(Enum):
    PENDING = "pending"
    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class ServerSetup:
    guild_id: int
    state: SetupState
    started_at: float
    completed_at: Optional[float]
    steps_completed: List[str]
    config: dict


class AutoSetup:
    def __init__(self, bot):
        self.bot = bot
        self._pending_setups: Dict[int, ServerSetup] = {}
        self._setup_messages: Dict[int, int] = {}
        self._startup_guilds = set()

    async def on_guild_join(self, guild: discord.Guild):
        logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id})")
        
        self._pending_setups[guild.id] = ServerSetup(
            guild_id=guild.id,
            state=SetupState.PENDING,
            started_at=time.time(),
            completed_at=None,
            steps_completed=[],
            config={}
        )
        
        await self._send_welcome_dm(guild)
        
        await self._initialize_server_data(guild)

    async def _send_welcome_dm(self, guild: discord.Guild):
        owner = guild.owner
        
        if not owner:
            return
        
        embed = discord.Embed(
            title="🤖 Welcome to Immortal AI!",
            description=f"I've been added to **{guild.name}**!",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="What I Do",
            value="I'm an AI-powered Discord bot that can build features for your server using natural language. Just use `/bot` to tell me what you want!",
            inline=False
        )
        
        embed.add_field(
            name="Quick Start",
            value="• `/bot` - Tell me what to build\n• `/help` - See all commands\n• `/status` - Check system health",
            inline=False
        )
        
        embed.add_field(
            name="Auto-Setup",
            value="Would you like me to automatically set up basic features for your server?",
            inline=False
        )
        
        view = discord.ui.View()
        
        setup_btn = discord.ui.Button(
            label="Run Auto-Setup",
            style=discord.ButtonStyle.success,
            custom_id="auto_setup_run"
        )
        
        skip_btn = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            custom_id="auto_setup_skip"
        )
        
        async def setup_callback(interaction: discord.Interaction):
            if interaction.user.id != owner.id:
                await interaction.response.send_message("Only the server owner can run setup.", ephemeral=True)
                return
            
            await interaction.response.send_message("🚀 Running auto-setup...", ephemeral=True)
            await self._run_auto_setup(guild, interaction.user)
        
        async def skip_callback(interaction: discord.Interaction):
            if interaction.user.id != owner.id:
                await interaction.response.send_message("Only the server owner can skip.", ephemeral=True)
                return
            
            self._pending_setups[guild.id].state = SetupState.SKIPPED
            await interaction.response.send_message("✅ Setup skipped. Use `/bot` anytime to create features!", ephemeral=True)
        
        setup_btn.callback = setup_callback
        skip_btn.callback = skip_callback
        
        view.add_item(setup_btn)
        view.add_item(skip_btn)
        
        try:
            await owner.send(embed=embed, view=view)
            logger.info(f"Sent welcome DM to {owner} for guild {guild.id}")
        except Exception as e:
            logger.error(f"Failed to send welcome DM: {e}")
            try:
                system_channel = guild.system_channel
                if system_channel:
                    await system_channel.send(embed=embed, view=view)
            except:
                pass

    async def _initialize_server_data(self, guild: discord.Guild):
        default_config = {
            "prefix": "!",
            "log_channel": None,
            "report_channel": None,
            "welcome_channel": None,
            "welcome_message": "Welcome {user} to {server}!",
            "leave_message": "{user} left {server}",
            "moderation_config": {
                "enabled": False,
                "sensitivity": "medium"
            },
            "conflict_resolution_config": {
                "enabled": True,
                "sensitivity": "medium",
                "auto_intervene": True,
                "notify_mods": True
            },
            "community_health_config": {
                "enabled": True,
                "analysis_interval_hours": 24,
                "health_reports_enabled": True
            },
            "leveling_config": {
                "enabled": False,
                "xp_per_message": 1,
                "xp_per_voice_minute": 0.5
            },
            "economy_config": {
                "enabled": False,
                "daily_reward": 100
            }
        }
        
        for key, value in default_config.items():
            dm.update_guild_data(guild.id, key, value)
        
        logger.info(f"Initialized default data for guild {guild.id}")

    async def _run_auto_setup(self, guild: discord.Guild, owner: discord.Member):
        setup = self._pending_setups.get(guild.id)
        if not setup:
            return
        
        setup.state = SetupState.STARTED
        setup.started_at = time.time()
        
        results = []
        
        try:
            result = await self._setup_welcome_system(guild)
            results.append(("Welcome System", result))
            setup.steps_completed.append("welcome")
        except Exception as e:
            logger.error(f"Welcome setup failed: {e}")
            results.append(("Welcome System", False))
        
        try:
            result = await self._setup_logging(guild)
            results.append(("Logging System", result))
            setup.steps_completed.append("logging")
        except Exception as e:
            logger.error(f"Logging setup failed: {e}")
            results.append(("Logging System", False))
        
        try:
            result = await self._setup_leveling(guild)
            results.append(("Leveling System", result))
            setup.steps_completed.append("leveling")
        except Exception as e:
            logger.error(f"Leveling setup failed: {e}")
            results.append(("Leveling System", False))
        
        try:
            result = await self._setup_basic_moderation(guild)
            results.append(("Basic Moderation", result))
            setup.steps_completed.append("moderation")
        except Exception as e:
            logger.error(f"Moderation setup failed: {e}")
            results.append(("Basic Moderation", False))
        
        setup.state = SetupState.COMPLETED
        setup.completed_at = time.time()
        
        await self._send_setup_results(guild, owner, results)

    async def _setup_welcome_system(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Welcome")
        if not category:
            category = await guild.create_category("Welcome")
        
        welcome_channel = discord.utils.get(guild.text_channels, name="welcome")
        if not welcome_channel:
            welcome_channel = await guild.create_text_channel("welcome", category=category)
            await welcome_channel.send("👋 Welcome channel set up! I'll send welcome messages here.")
        
        dm.update_guild_data(guild.id, "welcome_channel", welcome_channel.id)
        
        return True

    async def _setup_logging(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Logs")
        if not category:
            category = await guild.create_category("Logs")
        
        log_channel = discord.utils.get(guild.text_channels, name="bot-logs")
        if not log_channel:
            log_channel = await guild.create_text_channel("bot-logs", category=category)
            await log_channel.send("📝 I'll log moderator actions here.")
        
        dm.update_guild_data(guild.id, "log_channel", log_channel.id)
        
        return True

    async def _setup_leveling(self, guild: discord.Guild) -> bool:
        leveling_config = {
            "enabled": True,
            "xp_per_message": 1,
            "xp_per_voice_minute": 0.5,
            "level_roles": {
                "5": "Newcomer",
                "10": "Regular",
                "25": "Veteran",
                "50": "Elite"
            }
        }
        
        dm.update_guild_data(guild.id, "leveling_config", leveling_config)
        
        for level, role_name in leveling_config.get("level_roles", {}).items():
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                role = await guild.create_role(name=role_name)
        
        return True

    async def _setup_basic_moderation(self, guild: discord.Guild) -> bool:
        mod_config = {
            "enabled": True,
            "ai_enabled": True,
            "sensitivity": "medium",
            "auto_moderation": True
        }
        
        dm.update_guild_data(guild.id, "moderation_config", mod_config)
        
        return True

    async def _send_setup_results(self, guild: discord.Guild, owner: discord.Member, results: List[tuple]):
        success_count = sum(1 for _, success in results if success)
        
        embed = discord.Embed(
            title="✅ Auto-Setup Complete!",
            description=f"Successfully set up **{success_count}/{len(results)}** features for **{guild.name}**",
            color=discord.Color.green() if success_count == len(results) else discord.Color.orange()
        )
        
        for name, success in results:
            status = "✅" if success else "❌"
            embed.add_field(name=f"{status} {name}", value="Completed" if success else "Failed", inline=True)
        
        embed.add_field(
            name="Next Steps",
            value="• Use `/bot` to create more features\n• Check `/help` for all commands\n• Run `/config` to adjust settings",
            inline=False
        )
        
        embed.set_footer(text="Immortal AI • Auto-Setup")
        
        try:
            await owner.send(embed=embed)
        except:
            system_channel = guild.system_channel
            if system_channel:
                await system_channel.send(embed=embed)

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")
        
        if guild.id in self._pending_setups:
            del self._pending_setups[guild.id]

    def get_setup_status(self, guild_id: int) -> Optional[ServerSetup]:
        return self._pending_setups.get(guild_id)


class SetupView(discord.ui.View):
    def __init__(self, auto_setup: AutoSetup, guild: discord.Guild):
        super().__init__(timeout=300)
        self.auto_setup = auto_setup
        self.guild = guild
        
        setup_btn = discord.ui.Button(
            label="Auto-Setup",
            style=discord.ButtonStyle.primary,
            custom_id="guild_setup"
        )
        setup_btn.callback = self.setup_callback
        self.add_item(setup_btn)
    
    async def setup_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return
        
        await interaction.response.defer()
        await self.auto_setup._run_auto_setup(self.guild, interaction.user)
