import os
import re
from typing import Dict, List, Any
import io
import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import datetime as dt
import random
import signal
import json
import time
from dotenv import load_dotenv
from data_manager import dm
from logger import logger
from task_scheduler import TaskScheduler
import traceback

# Import all system modules
from modules import (
    economy, leveling, verification, anti_raid, guardian, welcome_leave,
    tickets, suggestions, reminders, giveaways, announcements, auto_responder,
    reaction_roles, reaction_menus, role_buttons, moderation, logging_mod,
    mod_logging, warnings, staff_promo, staff_shifts, staff_reviews,
    starboard, ai_chat, applications, appeals, modmail, auto_setup,
    config_panels
)

load_dotenv()

class MiroBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        intents.guilds = True
        intents.reactions = True
        intents.message_content = True

        super().__init__(
            command_prefix=self.get_dynamic_prefix,
            intents=intents,
            help_command=None
        )

        # Initialize all systems
        self.economy = economy.EconomySystem(self)
        self.leveling = leveling.LevelingSystem(self)
        self.verification = verification.VerificationSystem(self)
        self.anti_raid = anti_raid.AntiRaidSystem(self)
        self.guardian = guardian.GuardianSystem(self)
        self.welcome_leave = welcome_leave.WelcomeLeaveSystem(self)
        self.tickets = tickets.TicketSystem(self)
        self.suggestions = suggestions.SuggestionSystem(self)
        self.reminders = reminders.ReminderSystem(self)
        self.giveaways = giveaways.GiveawaySystem(self)
        self.announcements = announcements.AnnouncementSystem(self)
        self.auto_responder = auto_responder.AutoResponderSystem(self)
        self.reaction_roles = reaction_roles.ReactionRoleSystem(self)
        self.reaction_menus = reaction_menus.ReactionMenuSystem(self)
        self.role_buttons = role_buttons.RoleButtonSystem(self)
        self.moderation = moderation.ModerationSystem(self)
        self.logging_system = logging_mod.LoggingSystem(self)
        self.mod_logging = mod_logging.ModLoggingSystem(self)
        self.warnings = warnings.WarningSystem(self)
        self.staff_promo = staff_promo.StaffPromotionSystem(self)
        self.staff_shifts = staff_shifts.StaffShiftSystem(self)
        self.staff_reviews = staff_reviews.StaffReviewSystem(self)
        self.starboard = starboard.StarboardSystem(self)
        self.ai_chat = ai_chat.AIChatSystem(self)
        self.applications = applications.ApplicationSystem(self)
        self.appeals = appeals.AppealSystem(self)
        self.modmail = modmail.ModmailSystem(self)
        self.auto_setup = auto_setup.AutoSetupSystem(self)
        self.intelligence = intelligence.ServerIntelligence(self)

        # Task scheduler for reminders, giveaways, etc.
        self.task_scheduler = TaskScheduler(self)

        # State for immortal persistence
        self._background_tasks_started = False
        self._persistent_views_registered = False

    async def get_dynamic_prefix(self, bot, message):
        if not message.guild:
            return "!"
        return dm.get_guild_data(message.guild.id, "prefix", "!")

    async def setup_hook(self):
        """Initialize bot systems and restore immortal state."""
        logger.info("Starting Miro Bot setup...")

        # Load slash commands
        await self.load_extension('modules.slash_commands')

        # Register persistent views for immortal buttons
        await self._register_persistent_views()

        # Start background tasks
        await self._start_background_tasks()

        # Restore scheduled tasks from disk
        await self._restore_scheduled_tasks()

        logger.info("Miro Bot setup complete - all systems immortal!")

    async def _register_persistent_views(self):
        """Register all persistent views that survive bot restarts."""
        if self._persistent_views_registered:
            return

        # Register all system persistent views
        views_to_register = [
            # Auto setup buttons
            auto_setup.VerifyButton(),
            auto_setup.AcceptRulesButton(),
            auto_setup.CreateTicketButton(),
            auto_setup.SuggestionButton(),
            auto_setup.ApplyStaffButton(),

            # System specific views
            self.verification.get_persistent_views(),
            self.tickets.get_persistent_views(),
            self.suggestions.get_persistent_views(),
            self.giveaways.get_persistent_views(),
            self.applications.get_persistent_views(),
            self.appeals.get_persistent_views(),
            self.modmail.get_persistent_views(),
            self.welcome_leave.get_persistent_views(),
        ]

        # Flatten the list and register
        for view in views_to_register:
            if isinstance(view, list):
                for v in view:
                    self.add_view(v)
            else:
                self.add_view(view)

        self._persistent_views_registered = True
        logger.info("Persistent views registered")

    async def _start_background_tasks(self):
        """Start all background monitoring and automation tasks."""
        if self._background_tasks_started:
            return

        # Start system monitors (sync methods - no await)
        self.anti_raid.start_monitoring()
        self.staff_reviews.start_tasks()
        self.staff_shifts.start_tasks()

        # Start async monitoring tasks
        asyncio.create_task(self._start_async_monitors())

        # Start cleanup tasks
        asyncio.create_task(self._cleanup_expired_sessions())
        asyncio.create_task(self._auto_backup_loop())

        self._background_tasks_started = True
        logger.info("Background tasks started")

    async def _start_async_monitors(self):
        """Start all async monitoring tasks."""
        await self.guardian.start_monitoring()
        await self.giveaways.start_monitoring()
        await self.reminders.start_monitoring()
        await self.announcements.start_monitoring()
        self.intelligence.start_monitoring()
        logger.info("Async monitors started")

    async def _restore_scheduled_tasks(self):
        """Restore reminders, giveaways, and other scheduled tasks from disk."""
        try:
            # Restore reminders
            reminders_data = dm.load_json("scheduled_reminders", default=[])
            for reminder in reminders_data:
                if reminder.get("scheduled_time", 0) > time.time():
                    await self.task_scheduler.schedule_task(
                        reminder["scheduled_time"],
                        self.reminders.send_reminder,
                        reminder
                    )

            # Restore giveaways
            giveaways_data = dm.load_json("active_giveaways", default=[])
            for giveaway in giveaways_data:
                if giveaway.get("end_time", 0) > time.time():
                    await self.task_scheduler.schedule_task(
                        giveaway["end_time"],
                        self.giveaways.end_giveaway,
                        giveaway
                    )

            logger.info("Scheduled tasks restored")
        except Exception as e:
            logger.error(f"Failed to restore scheduled tasks: {e}")

    async def _cleanup_expired_sessions(self):
        """Clean up expired AI sessions and temporary data."""
        while True:
            try:
                current_time = time.time()
                # Clean up expired sessions from various systems
                expired_sessions = []

                # Add cleanup logic for different session types as needed
                # For now, just sleep
                await asyncio.sleep(3600)  # Clean every hour
            except Exception as e:
                logger.error(f"Session cleanup error: {e}")
                await asyncio.sleep(3600)

    async def _auto_backup_loop(self):
        """Automatic data backup every 6 hours."""
        while True:
            try:
                dm.backup_data()
                logger.info("Automatic backup completed")
            except Exception as e:
                logger.error(f"Auto backup failed: {e}")
            await asyncio.sleep(21600)  # 6 hours

    async def on_ready(self):
        """Bot is ready and connected."""
        logger.info(f"Miro Bot ready as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")

        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()

        # One-time setup tasks
        if not hasattr(self, '_guild_setup_done'):
            self._guild_setup_done = True
            asyncio.create_task(self._setup_guild_data())

    async def _setup_guild_data(self):
        """Initialize data for guilds the bot is in."""
        for guild in self.guilds:
            # Ensure basic data structure exists
            dm.get_guild_data(guild.id, "initialized", True)

            # Set up any missing system data
            systems = [
                "economy", "leveling", "verification", "anti_raid", "guardian",
                "tickets", "suggestions", "reminders", "giveaways", "announcements",
                "auto_responder", "reaction_roles", "moderation", "warnings",
                "staff_shifts", "staff_reviews", "starboard", "ai_chat"
            ]

            for system in systems:
                config_key = f"{system}_config"
                if not dm.get_guild_data(guild.id, config_key):
                    dm.update_guild_data(guild.id, config_key, {"enabled": False})

        logger.info("Guild data initialization complete")

    def _setup_signal_handlers(self):
        """Set up graceful shutdown handlers."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))
            logger.info("Signal handlers set up")
        except Exception as e:
            logger.warning(f"Could not set up signal handlers: {e}")

    async def _graceful_shutdown(self):
        """Clean shutdown with data persistence."""
        logger.info("Starting graceful shutdown...")

        try:
            # Save all data
            dm.backup_data()

            # Stop background tasks
            await self.task_scheduler.stop()

            # Close bot connection
            await self.close()

            logger.info("Graceful shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            import sys
            sys.exit(0)

    async def on_message(self, message):
        """Handle incoming messages."""
        if message.author.bot:
            return

        # Handle DMs through modmail
        if isinstance(message.channel, discord.DMChannel):
            await self.modmail.handle_dm(message)
            return

        # Process commands
        await self.process_commands(message)

        # Handle passive system triggers
        await self._handle_passive_systems(message)

    async def _handle_passive_systems(self, message):
        """Handle systems that react to messages passively."""
        try:
            # Leveling XP
            await self.leveling.handle_message(message)

            # Economy passive income
            await self.economy.handle_message(message)

            # Auto responder
            await self.auto_responder.handle_message(message)

            # Anti-raid monitoring
            await self.anti_raid.handle_message(message)

            # Guardian bot token detection
            await self.guardian.handle_message(message)

            # Staff shift tracking
            await self.staff_shifts.handle_message(message)

            # AI chat channels
            await self.ai_chat.handle_message(message)

            # Trigger roles
            await self.welcome_leave.handle_trigger_roles(message)

        except Exception as e:
            logger.error(f"Passive system error: {e}")
            # Don't let one system failure break others

    async def on_member_join(self, member):
        """Handle member joins."""
        try:
            await self.verification.handle_member_join(member)
            await self.welcome_leave.handle_member_join(member)
            await self.anti_raid.handle_join(member)
        except Exception as e:
            logger.error(f"Member join error: {e}")

    async def on_member_remove(self, member):
        """Handle member leaves."""
        try:
            await self.welcome_leave.handle_member_remove(member)
            await self.anti_raid.handle_member_remove(member)
        except Exception as e:
            logger.error(f"Member remove error: {e}")

    async def on_reaction_add(self, reaction, user):
        """Handle reaction adds."""
        try:
            await self.starboard.handle_reaction_add(reaction, user)
            await self.suggestions.handle_reaction_add(reaction, user)
        except Exception as e:
            logger.error(f"Reaction add error: {e}")

    async def on_guild_join(self, guild):
        """Handle joining a new guild."""
        logger.info(f"Joined new guild: {guild.name} ({guild.id})")
        try:
            await self.auto_setup.initialize_guild(guild)
        except Exception as e:
            logger.error(f"Guild join setup error: {e}")

    async def on_error(self, event, *args, **kwargs):
        """Global error handler."""
        logger.error(f"Event error in {event}: {traceback.format_exc()}")

# Create and run the bot
if __name__ == "__main__":
    bot = MiroBot()
    bot.run(os.getenv("DISCORD_TOKEN"))