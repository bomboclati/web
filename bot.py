import os
import io
import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import datetime
import random
import signal
import json
import time
from dotenv import load_dotenv
from data_manager import dm
from history_manager import history_manager
from ai_client import AIClient, SYSTEM_PROMPT
from logger import logger
from task_scheduler import TaskScheduler
from vector_memory import vector_memory
from actions import ActionHandler

# Import Modules
from modules.economy import Economy
from modules.leveling import Leveling
from modules.staff_system import StaffSystem
from modules.appeals import Appeals
from modules.trigger_roles import TriggerRoles
from modules.moderation import ContextualModeration
from modules.events import EventScheduler
from modules.intelligence import ServerIntelligence
from modules.gamification import AdaptiveGamification
from modules.tickets import AdvancedTickets
from modules.voice_system import VoiceActivitySystem
from modules.content_generator import ContentGenerator
from modules.tournaments import TournamentSystem
from modules.chat_channels import AIChatSystem
from modules.starboard import StarboardSystem
from modules.reminders import ReminderSystem
from modules.welcome_leave import WelcomeLeaveSystem
from modules.giveaways import GiveawaySystem
from modules.anti_raid import AntiRaidSystem
from modules.auto_publisher import AutoPublisher
from modules.achievements import AchievementSystem
from modules.staff_promo import StaffPromotionSystem
from modules.staff_extras import StaffExtras, StaffExtrasCommands
from modules.staff_reviews import StaffReviewSystem
from modules.staff_shift import StaffShiftSystem
from modules.auto_announcer import AutoAnnouncer
from modules.conflict_resolution import ConflictResolution
from modules.community_health import CommunityHealth
from modules.auto_setup import AutoSetup
from modules.promotion_service import PromotionService

load_dotenv()

class ImmortalBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        
        super().__init__(
            command_prefix=self.get_dynamic_prefix,
            intents=intents,
            help_command=None
        )
        
        self.ai = AIClient(
            api_key=os.getenv("AI_API_KEY"),
            provider=os.getenv("AI_PROVIDER", "openrouter"),
            model=os.getenv("AI_MODEL")
        )
        
        # State caches (recovered on startup)
        self.custom_commands = {} # guild_id -> {prefix_cmd_name: code}
        self.active_tasks = {}    # guild_id -> {task_id: task_obj}
        self.pending_confirms = {} # user_id -> {action_data, message_obj}
        self._bot_cooldowns = {}  # user_id -> timestamp
        self._bot_cooldown_seconds = 30
        self.ai_sessions = {}     # user_id -> {messages: [...], last_interaction: interaction, original_request: str}
        
        # Internal Systems
        self.economy = Economy(self)
        self.leveling = Leveling(self)
        self.appeals = Appeals(self)
        self.trigger_roles = TriggerRoles(self)
        self.scheduler = TaskScheduler(self)
        self.moderation = ContextualModeration(self)
        self.events = EventScheduler(self)
        self.intelligence = ServerIntelligence(self)
        self.gamification = AdaptiveGamification(self)
        self.tickets = AdvancedTickets(self)
        self.voice_system = VoiceActivitySystem(self)
        self.content_generator = ContentGenerator(self)
        self.tournaments = TournamentSystem(self)
        self.chat_channels = AIChatSystem(self)
        self.starboard = StarboardSystem(self)
        self.reminders = ReminderSystem(self)
        self.welcome_leave = WelcomeLeaveSystem(self)
        self.giveaways = GiveawaySystem(self)
        self.anti_raid = AntiRaidSystem(self)
        self.auto_publisher = AutoPublisher(self)
        self.achievements = AchievementSystem(self)
        self.promotion_service = PromotionService()
        self.staff_promo = StaffPromotionSystem(self)
        self.staff_extras = StaffExtras(self)
        self.staff_reviews = StaffReviewSystem(self)
        self.staff_shift = StaffShiftSystem(self)
        self.auto_announcer = AutoAnnouncer(self)
        self.conflict_resolution = ConflictResolution(self)
        self.community_health = CommunityHealth(self)
        self.auto_setup = AutoSetup(self)

    async def get_dynamic_prefix(self, bot, message):
        if not message.guild:
            return "!"
        return dm.get_guild_data(message.guild.id, "prefix", "!")

    async def setup_hook(self):
        logger.info("Recovering immortal state...")
        await self.tree.sync()
        logger.info("Slash commands synced.")
        logger.info("Restoring trigger role presence monitoring...")
        await self.scheduler.start()

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s) (IMMORTAL)", self.user, self.user.id)
        self.loop.create_task(self._auto_backup_loop())
        self.loop.create_task(self._cleanup_expired_sessions())
        self.loop.create_task(self._command_refinement_loop())
        self._setup_crash_recovery()
        self._setup_signal_handlers()
        self.loop.create_task(self._check_new_guilds())

    async def _check_new_guilds(self):
        """Check for any guilds the bot just joined"""
        await asyncio.sleep(5)
        for guild in self.guilds:
            if guild.id not in self.auto_setup._pending_setups:
                await self.auto_setup.on_guild_join(guild)

    async def _cleanup_expired_sessions(self):
        """Remove expired AI conversation sessions."""
        while True:
            now = time.time()
            expired = [uid for uid, sess in self.ai_sessions.items() if sess.get("expires_at", 0) < now]
            for uid in expired:
                del self.ai_sessions[uid]
                logger.info("Expired AI session for user %d", uid)
            await asyncio.sleep(60)

    def _setup_crash_recovery(self):
        """Check for incomplete setups from previous crashes and clean up."""
        pending_setups = dm.load_json("pending_setups", default={})
        for setup_id, setup_data in pending_setups.items():
            logger.warning("Found incomplete setup %s from crash - cleaning up", setup_id)
            guild_id = setup_data.get("guild_id")
            actions_taken = setup_data.get("actions_taken", [])
            self._cleanup_crash_setup(guild_id, actions_taken)
            del pending_setups[setup_id]
        if pending_setups:
            dm.save_json("pending_setups", pending_setups)
        logger.info("Crash recovery check completed")

    def _cleanup_crash_setup(self, guild_id: int, actions_taken: list):
        """Clean up half-built setups from a crash."""
        guild = self.get_guild(guild_id)
        if not guild:
            return
        for action in actions_taken:
            try:
                if action.get("type") == "channel" and "id" in action:
                    channel = guild.get_channel(action["id"])
                    if channel:
                        asyncio.create_task(channel.delete())
                        logger.info("Cleaned up orphaned channel: %s", channel.name)
                elif action.get("type") == "role" and "id" in action:
                    role = guild.get_role(action["id"])
                    if role:
                        asyncio.create_task(role.delete())
                        logger.info("Cleaned up orphaned role: %s", role.name)
            except Exception as e:
                logger.error("Failed to clean up crash artifact: %s", e)

    def _setup_signal_handlers(self):
        """Set up graceful shutdown on SIGINT/SIGTERM."""
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))
            logger.info("Signal handlers registered for graceful shutdown")
        except (NotImplementedError, ValueError):
            logger.warning("Signal handlers not supported on this platform")

    async def _graceful_shutdown(self):
        """Flush all state and shut down cleanly."""
        logger.info("Graceful shutdown initiated...")
        try:
            await self.scheduler.stop()
            dm.backup_data()
            logger.info("State flushed to disk, backup completed")
            logger.info("Shutting down bot...")
            await self.close()
        except Exception as e:
            logger.error("Error during graceful shutdown: %s", e)
        finally:
            import sys
            sys.exit(0)

    async def _auto_backup_loop(self):
        """Run automatic backups every 6 hours."""
        backup_interval = int(os.getenv("BACKUP_INTERVAL_HOURS", 6)) * 3600
        await asyncio.sleep(60)
        while True:
            try:
                dm.backup_data()
                logger.info("Automatic backup completed.")
            except Exception as e:
                logger.error("Automatic backup failed: %s", e)
            await asyncio.sleep(backup_interval)

    async def _command_refinement_loop(self):
        """Periodically analyze command usage and generate improvement suggestions."""
        await asyncio.sleep(300)  # Wait 5 minutes after startup
        analysis_interval = int(os.getenv("COMMAND_ANALYSIS_INTERVAL_HOURS", 24)) * 3600
        while True:
            try:
                await self.analyze_command_usage_and_suggest_improvements()
            except Exception as e:
                logger.error("Command refinement analysis failed: %s", e)
            await asyncio.sleep(analysis_interval)

    async def _self_reflect_on_response(self, guild_id: int, user_id: int, user_input: str, 
                                      bot_response: str, reasoning: str, walkthrough: str):
        """
        Self-reflection mechanism to improve future responses.
        Analyzes the current interaction and stores insights for future improvement.
        """
        try:
            # Create a reflection prompt for the AI to analyze its own response
            reflection_prompt = f"""
You are reviewing a previous interaction to improve future responses.

USER INPUT: {user_input}

YOUR RESPONSE: {bot_response}

YOUR REASONING: {reasoning}

YOUR WALKTHROUGH: {walkthrough}

Please provide a brief self-reflection on:
1. What went well in this response?
2. What could be improved?
3. Any patterns or insights for future similar interactions?

Keep your reflection concise (2-3 sentences) and focus on actionable improvements.
"""
            
            # Get reflection from AI (using a lighter weight prompt)
            reflection_result = await bot.ai.chat(
                guild_id=guild_id,
                user_id=user_id,
                user_input=reflection_prompt,
                system_prompt="You are an AI analyzing your own past responses to improve future performance. Be concise and actionable."
            )
            
            reflection_text = reflection_result.get("summary", "No reflection generated.")
            
            # Store reflection in vector memory for future reference
            vector_memory.store_conversation(
                guild_id=guild_id,
                user_id=user_id,
                user_message=f"SELF_REFLECTION_ON: {user_input[:100]}...",
                bot_response=reflection_text,
                reasoning="Self-reflection on previous interaction",
                walkthrough="Analyzing response quality for improvement",
                importance_score=0.7  # Reflections are moderately important for learning
            )
            
            logger.debug(f"Stored self-reflection for user {user_id} in guild {guild_id}")
            
        except Exception as e:
            logger.error(f"Error in self-reflection mechanism: {e}")

    async def on_message(self, message):
        if message.author.bot:
            return

        # 1. Passive Systems (XP & Triggers)
        await self.leveling.handle_message(message)
        await self.trigger_roles.handle_message(message)
        await self.moderation.analyze_message(message)
        await self.intelligence.track_message(message)
        await self.conflict_resolution.analyze_message(message)
        await self.community_health.analyze_interaction(message)
        
        # 2. AI Chat Channels (if message is in an AI chat channel)
        await self.chat_channels.handle_message(message)

        # 2. Prefix Commands
        prefix = await self.get_dynamic_prefix(self, message)
        if message.content.startswith(prefix):
            cmd_content = message.content[len(prefix):].strip()
            
            # Handle !suggest command
            if cmd_content.startswith("suggest"):
                await self._handle_suggest_command(message, cmd_content)
                return
            
            # Handle memory export/import commands
            if cmd_content.startswith("exportmemory"):
                await self._handle_export_memory(message)
                return
            
            if cmd_content.startswith("importmemory"):
                await self._handle_import_memory(message)
                return
            
            if cmd_content.startswith("scheduled"):
                await self._handle_scheduled_actions(message, cmd_content)
                return
            
            if cmd_content.strip() == "help":
                from actions import ActionHandler
                handler = ActionHandler(self)
                await handler.handle_help_all(message)
                return
            
            # Handle staff commands
            if any(cmd_content.startswith(cmd) for cmd in ["staffleaderboard", "promotionhistory", "trainingtasks", "appeal"]):
                await self._handle_staff_command(message, cmd_content)
                return
            
            guild_cmds = dm.get_guild_data(message.guild.id, "custom_commands", {})
            
            matched_cmd = None
            matched_data = None
            
            for cmd_name in list(guild_cmds.keys()):
                if cmd_content == cmd_name or cmd_content.startswith(cmd_name + " "):
                    matched_cmd = cmd_name
                    matched_data = guild_cmds[cmd_name]
                    break
            
            if matched_cmd:
                parts = cmd_content.split()
                cmd_name = matched_cmd
                # Track command chain (what was run before this)
                prev_cmd = self.track_command_chain(message.author.id, cmd_name)
                
                # Track command with context for AI improvement
                context = {
                    "user_id": message.author.id,
                    "guild_id": message.guild.id,
                    "channel_id": message.channel.id,
                    "message_content": message.content,
                    "timestamp": message.created_at.timestamp(),
                    "previous_command": prev_cmd
                }
                self._track_command_usage(message.guild.id, cmd_name, True, context)
                from actions import ActionHandler
                handler = ActionHandler(self)
                await handler.execute_custom_command(message, matched_data, cmd_name)
                return

        await self.process_commands(message)

    def _track_command_usage(self, guild_id: int, cmd_name: str, success: bool = True, context: dict = None):
        """Track ! command usage for AI feedback loop with enhanced data."""
        usage = dm.get_guild_data(guild_id, "command_usage", {})
        if cmd_name not in usage:
            usage[cmd_name] = {
                "count": 0, 
                "last_used": 0, 
                "users": [], 
                "failures": 0,
                "contexts": [],
                "command_chains": []
            }
        usage[cmd_name]["count"] = usage[cmd_name].get("count", 0) + 1
        if not success:
            usage[cmd_name]["failures"] = usage[cmd_name].get("failures", 0) + 1
        usage[cmd_name]["last_used"] = time.time()
        
        # Track user usage (last 100 users)
        users = usage[cmd_name].get("users", [])
        if not isinstance(users, list):
            users = []
        user_id_str = str(guild_id) + "_" + str(context.get("user_id", "unknown")) if context else str(guild_id) + "_unknown"
        if user_id_str not in users[-100:]:  # Avoid duplicates in recent history
            users.append(user_id_str)
        usage[cmd_name]["users"] = users[-100:]
        
        # Track context if provided
        if context:
            contexts = usage[cmd_name].get("contexts", [])
            if not isinstance(contexts, list):
                contexts = []
            # Keep only last 50 contexts
            contexts.append({
                "timestamp": time.time(),
                "user_id": context.get("user_id"),
                "guild_id": guild_id,
                "data": context.get("data", {})
            })
            usage[cmd_name]["contexts"] = contexts[-50:]
            
            # Track command chains (what command was run before this one)
            prev_cmd = context.get("previous_command")
            if prev_cmd:
                chains = usage[cmd_name].get("command_chains", [])
                if not isinstance(chains, list):
                    chains = []
                chains.append({
                    "previous_command": prev_cmd,
                    "timestamp": time.time(),
                    "user_id": context.get("user_id")
                })
                # Keep only last 100 chains
                usage[cmd_name]["command_chains"] = chains[-100:]
        
        dm.update_guild_data(guild_id, "command_usage", usage)
        
        # Also track globally for cross-server intelligence
        self._track_global_command_usage(cmd_name, success, context)
    
    def _track_global_command_usage(self, cmd_name: str, success: bool = True, context: dict = None):
        """Track command usage globally for cross-server intelligence."""
        global_usage = dm.load_json("global_command_usage", default={})
        if cmd_name not in global_usage:
            global_usage[cmd_name] = {
                "total_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "guilds_used": set(),
                "last_used": 0
            }
        
        global_usage[cmd_name]["total_count"] = global_usage[cmd_name].get("total_count", 0) + 1
        if success:
            global_usage[cmd_name]["success_count"] = global_usage[cmd_name].get("success_count", 0) + 1
        else:
            global_usage[cmd_name]["failure_count"] = global_usage[cmd_name].get("failure_count", 0) + 1
            
        global_usage[cmd_name]["last_used"] = time.time()
        
        # Track which guilds use this command (anonymized)
        guilds_used = global_usage[cmd_name].get("guilds_used", set())
        if isinstance(guilds_used, list):
            guilds_used = set(guilds_used)
        if context and context.get("guild_id"):
            guilds_used.add(str(context["guild_id"]))  # Store as string for JSON serialization
        global_usage[cmd_name]["guilds_used"] = list(guilds_used)[-1000:]  # Keep last 1000 guilds
        
        dm.save_json("global_command_usage", global_usage)
    
    def track_failed_command(self, guild_id: int, cmd_name: str, user_id: int = None, error_message: str = None):
        """Track a failed command execution for AI analysis."""
        self._track_command_usage(guild_id, cmd_name, success=False, context={
            "user_id": user_id,
            "guild_id": guild_id,
            "error": error_message
        })
        
        # Also track in action_failures for the analysis system
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        if cmd_name not in failures:
            failures[cmd_name] = {"count": 0, "errors": [], "last_error": None}
        failures[cmd_name]["count"] += 1
        if error_message:
            failures[cmd_name]["last_error"] = error_message[:200]
            if len(failures[cmd_name]["errors"]) < 10:
                failures[cmd_name]["errors"].append(error_message[:200])
        dm.update_guild_data(guild_id, "action_failures", failures)
    
    # Store recent command executions for chain detection
    _recent_commands = {}  # user_id -> {"command": str, "timestamp": float}
    _command_chain_window = 60  # 60 seconds between commands counts as a chain
    
    def track_command_chain(self, user_id: int, command: str):
        """Track command chain - what command was run before this one."""
        now = time.time()
        prev_command = None
        
        if user_id in self._recent_commands:
            prev_data = self._recent_commands[user_id]
            if now - prev_data.get("timestamp", 0) < self._command_chain_window:
                prev_command = prev_data.get("command")
        
        # Update recent command
        self._recent_commands[user_id] = {"command": command, "timestamp": now}
        
        return prev_command
    
    async def _handle_suggest_command(self, message, cmd_content):
        guild = message.guild
        if not guild:
            return
        
        parts = cmd_content.split(None, 2)
        if len(parts) < 3:
            await message.channel.send("Usage: `!suggest <title> <description>`")
            return
        
        title = parts[1]
        description = parts[2]
        
        suggestions_channel_id = dm.get_guild_data(guild.id, "suggestions_channel")
        
        if not suggestions_channel_id:
            await message.channel.send("No suggestions channel set up yet!")
            return
        
        channel = guild.get_channel(suggestions_channel_id)
        if not channel:
            await message.channel.send("Suggestions channel not found!")
            return
        
        embed = discord.Embed(
            title=f"💡 {title}",
            description=description,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Suggested by {message.author}")
        embed.add_field(name="Status", value="⏳ Pending Review", inline=False)
        
        msg = await channel.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        
        await message.channel.send("✅ Suggestion submitted!")
    
    async def _handle_export_memory(self, message):
        """Handle !exportmemory command"""
        guild = message.guild
        if not guild:
            return
        
        try:
            export_data = dm.export_memory(guild.id)
            
            if not export_data.get("guilds"):
                await message.channel.send("No conversation data found.")
                return
            
            json_str = json.dumps(export_data, indent=2)
            bytes_io = io.BytesIO(json_str.encode('utf-8'))
            file = discord.File(bytes_io, filename=f"memory_export_{guild.id}_{datetime.now().strftime('%Y%m%d')}.json")
            
            await message.channel.send("📤 Here is your conversation memory export:", file=file)
            
        except Exception as e:
            logger.error(f"Memory export failed: {e}")
            await message.channel.send(f"Export failed: {str(e)}")

    async def _handle_import_memory(self, message):
        """Handle !importmemory command"""
        guild = message.guild
        if not guild:
            return
        
        if not message.attachments:
            await message.channel.send("Please attach a JSON export file.")
            return
        
        attachment = message.attachments[0]
        
        if not attachment.filename.endswith('.json'):
            await message.channel.send("Please attach a valid JSON file.")
            return
        
        try:
            content = await attachment.read()
            import_data = json.loads(content.decode('utf-8'))
            
            result = dm.import_memory(import_data, merge=True)
            
            if result["success"]:
                await message.channel.send(f"✅ Import complete! Imported {result['imported']} exchanges.")
            else:
                await message.channel.send(f"❌ Import failed: {result['errors']}")
                
        except json.JSONDecodeError:
            await message.channel.send("Invalid JSON file format.")
        except Exception as e:
            logger.error(f"Memory import failed: {e}")
            await message.channel.send(f"Import failed: {str(e)}")

    async def _handle_scheduled_actions(self, message, cmd_content):
        """Handle !scheduled command"""
        guild = message.guild
        if not guild:
            return
        
        parts = cmd_content.split()
        
        tasks = dm.load_json("ai_scheduled_tasks", default={})
        guild_tasks = {k: v for k, v in tasks.items() if v.get("guild_id") == guild.id}
        
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
            if not guild_tasks:
                await message.channel.send("No scheduled actions on this server.")
                return
            
            lines = []
            for task_name, task_data in guild_tasks.items():
                cron = task_data.get("cron", "N/A")
                enabled = "✅" if task_data.get("enabled", True) else "❌"
                action_type = task_data.get("action_type", "unknown")
                lines.append(f"**{task_name}** - {action_type} | {cron} | {enabled}")
            
            embed = discord.Embed(title="⏰ Scheduled AI Actions", color=discord.Color.blue())
            embed.description = "\n".join(lines)
            await message.channel.send(embed=embed)
            
        elif len(parts) >= 3:
            action = parts[1]
            name = parts[2]
            
            if action == "delete":
                if name in tasks:
                    del tasks[name]
                    dm.save_json("ai_scheduled_tasks", tasks)
                    await message.channel.send(f"✅ Deleted: {name}")
                else:
                    await message.channel.send(f"❌ Not found: {name}")
                    
            elif action == "enable":
                if name in tasks:
                    tasks[name]["enabled"] = True
                    dm.save_json("ai_scheduled_tasks", tasks)
                    await message.channel.send(f"✅ Enabled: {name}")
                else:
                    await message.channel.send(f"❌ Not found: {name}")
                    
            elif action == "disable":
                if name in tasks:
                    tasks[name]["enabled"] = False
                    dm.save_json("ai_scheduled_tasks", tasks)
                    await message.channel.send(f"✅ Disabled: {name}")
                else:
                    await message.channel.send(f"❌ Not found: {name}")
            else:
                await message.channel.send("Usage: `!scheduled list/delete/enable/disable <name>`")
        else:
            await message.channel.send("Usage: `!scheduled list` or `!scheduled delete <name>`")
    
    async def _handle_staff_command(self, message, cmd_content):
        """Handle staff extras commands"""
        parts = cmd_content.split()
        
        if not parts:
            return
        
        command = parts[0].lower()
        
        shift_commands = {
            "shift start": self.staff_shift.handle_shift_start,
            "shift end": self.staff_shift.handle_shift_end,
            "show shifts": self.staff_shift.handle_show_shifts,
        }
        
        if command in ["shift start", "shift end", "show shifts"]:
            func = shift_commands[command]
            await func(message)
            return
        
        shift_only_commands = {
            "start": self.staff_shift.handle_shift_start,
            "end": self.staff_shift.handle_shift_end,
            "show": self.staff_shift.handle_show_shifts,
        }
        
        if any(cmd_content.startswith(f"!{cmd}") for cmd in ["shift"]):
            if command == "shift" and len(parts) > 1:
                sub_cmd = parts[1].lower()
                if sub_cmd in shift_only_commands:
                    func = shift_only_commands[sub_cmd]
                    await func(message)
                    return
        
        activity_commands = {
            "logs": self.staff_shift.handle_activity_logs,
            "allactivity": self.staff_shift.handle_all_activity,
        }
        
        if command in ["logs", "allactivity"]:
            func = activity_commands[command]
            await func(message, parts)
            return
        
        announcer_commands = {
            "announce": self.auto_announcer.handle_announce_create,
            "announces": self.auto_announcer.handle_announce_list,
            "remind": self.auto_announcer.handle_remind,
            "remindme": self.auto_announcer.handle_reminders_list,
            "remind_user": self.auto_announcer.handle_remind_user,
        }
        
        if command in announcer_commands:
            func = announcer_commands[command]
            await func(message, parts)
            return
        
        task_commands = {
            "task": self.staff_shift.handle_task_assign,
            "tasks": self.staff_shift.handle_task_list,
            "complete": self.staff_shift.handle_task_complete,
        }
        
        if command in ["task", "tasks", "complete"]:
            func = task_commands[command]
            await func(message, parts)
            return
        
        warning_commands = {
            "warn": self.staff_shift.handle_warn,
            "warnings": self.staff_shift.handle_warnings,
        }
        
        if command in ["warn", "warnings"]:
            if not message.author.guild_permissions.administrator:
                return
            func = warning_commands[command]
            await func(message, parts)
            return
        
        staff_commands = {
            "staffleaderboard": self.staff_extras.handle_staff_leaderboard,
            "promotionhistory": self.staff_extras.handle_promotion_history,
            "trainingtasks": self.staff_extras.handle_training_tasks,
            "appeal": self.staff_extras.handle_appeal,
            "staffstats": self.staff_reviews.handle_staff_stats,
            "probation": self.staff_reviews.handle_probation_status,
            "vote": self.staff_reviews.handle_peer_vote,
        }
        
        if command in staff_commands:
            cmd_func = staff_commands[command]
            await cmd_func(message, parts)
    
    async def analyze_command_usage_and_suggest_improvements(self):
        """Periodically analyze command usage and suggest improvements."""
        try:
            logger.info("Starting command usage analysis for improvement suggestions...")
            
            # Get global command usage data
            global_usage = dm.load_json("global_command_usage", default={})
            
            # Get command failure data from all guilds
            command_failures = {}
            command_successes = {}
            command_chains = {}
            
            # Scan all guild data files for command usage patterns
            data_dir = "data"
            if os.path.exists(data_dir):
                for filename in os.listdir(data_dir):
                    if filename.startswith("guild_") and filename.endswith(".json"):
                        try:
                            guild_data = dm.load_json(filename[:-5])  # Remove .json extension
                            guild_id = filename[6:-5]  # Extract guild ID
                            
                            # Collect failure data
                            failures = guild_data.get("action_failures", {})
                            for cmd, data in failures.items():
                                if cmd not in command_failures:
                                    command_failures[cmd] = {"count": 0, "errors": []}
                                command_failures[cmd]["count"] += data.get("count", 0)
                                command_failures[cmd]["errors"].extend(data.get("errors", []))
                            
                            # Collect success data
                            successes = guild_data.get("action_successes", {})
                            for cmd, count in successes.items():
                                if cmd not in command_successes:
                                    command_successes[cmd] = 0
                                command_successes[cmd] += count
                            
                            # Collect command chains
                            usage = guild_data.get("command_usage", {})
                            for cmd, data in usage.items():
                                chains = data.get("command_chains", [])
                                if chains:
                                    if cmd not in command_chains:
                                        command_chains[cmd] = []
                                    command_chains[cmd].extend(chains)
                        except Exception as e:
                            logger.error("Error processing guild data file %s: %s", filename, e)
            
            # Generate improvement suggestions
            suggestions = []
            
            # Analyze high failure rate commands
            for cmd_name, usage_data in global_usage.items():
                total_count = usage_data.get("total_count", 0)
                failure_count = usage_data.get("failure_count", 0)
                if total_count >= 10:  # Only analyze commands with sufficient usage
                    failure_rate = failure_count / total_count if total_count > 0 else 0
                    if failure_rate > 0.3:  # More than 30% failure rate
                        suggestions.append({
                            "type": "high_failure_rate",
                            "command": cmd_name,
                            "failure_rate": failure_rate,
                            "total_uses": total_count,
                            "suggestion": f"Users frequently fail when using !{cmd_name}. Consider improving command clarity or adding better error handling.",
                            "confidence": min(0.9, failure_rate * 2)  # Higher failure rate = higher confidence
                        })
            
            # Analyze command chains for common patterns
            for cmd_name, chains in command_chains.items():
                if len(chains) >= 5:  # Need sufficient data
                    # Count what commands come before this one
                    prev_cmd_counts = {}
                    for chain in chains:
                        prev_cmd = chain.get("previous_command")
                        if prev_cmd:
                            prev_cmd_counts[prev_cmd] = prev_cmd_counts.get(prev_cmd, 0) + 1
                    
                    # Find most common previous command
                    if prev_cmd_counts:
                        most_common_prev = max(prev_cmd_counts, key=prev_cmd_counts.get)
                        count = prev_cmd_counts[most_common_prev]
                        if count >= 3 and count / len(chains) > 0.5:  # At least 3 times and >50% of the time
                            suggestions.append({
                                "type": "command_chain",
                                "command": cmd_name,
                                "previous_command": most_common_prev,
                                "frequency": count,
                                "total_chains": len(chains),
                                "suggestion": f"Users often run !{most_common_prev} before !{cmd_name}. Consider creating a command chain or adding !{most_common_prev} as a subcommand.",
                                "confidence": min(0.9, count / len(chains))
                            })
            
            # Analyze common error patterns
            for cmd_name, failure_data in command_failures.items():
                if failure_data["count"] >= 5:  # Need sufficient failure data
                    errors = failure_data["errors"]
                    if errors:
                        # Count error types
                        error_counts = {}
                        for error in errors:
                            # Simplify error message for grouping
                            simple_error = error.lower()
                            if "missing" in simple_error and "argument" in simple_error:
                                error_type = "missing_argument"
                            elif "not found" in simple_error:
                                error_type = "not_found"
                            elif "permission" in simple_error:
                                error_type = "permission"
                            else:
                                error_type = "other"
                            
                            error_counts[error_type] = error_counts.get(error_type, 0) + 1
                        
                        # Find most common error type
                        if error_counts:
                            most_common_error = max(error_counts, key=error_counts.get)
                            count = error_counts[most_common_error]
                            if count >= 3:  # At least 3 occurrences
                                suggestions.append({
                                    "type": "error_pattern",
                                    "command": cmd_name,
                                    "error_type": most_common_error,
                                    "frequency": count,
                                    "total_failures": failure_data["count"],
                                    "suggestion": self._generate_error_suggestion(cmd_name, most_common_error),
                                    "confidence": min(0.9, count / failure_data["count"]),
                                    "severity": count / failure_data["count"]  # For auto-action threshold
                                })
            
            # Save suggestions for review AND auto-apply high confidence ones
            if suggestions:
                # Sort by confidence and type priority
                suggestions.sort(key=lambda x: (x["confidence"], x["type"] == "high_failure_rate"), reverse=True)
                
                # Separate high confidence (auto-apply) and low confidence (store for review)
                auto_apply_threshold = 0.8
                auto_apply = []
                for_review = []
                
                for s in suggestions:
                    if s.get("confidence", 0) >= auto_apply_threshold:
                        auto_apply.append(s)
                    else:
                        for_review.append(s)
                
                # Auto-apply high confidence suggestions AND error prevention
                for s in auto_apply:
                    try:
                        cmd_name = s.get("command")
                        suggestion_text = s.get("suggestion", "")
                        suggestion_type = s.get("type")
                        error_type = s.get("error_type")
                        severity = s.get("severity", 0)
                        
                        logger.info(f"Auto-applying improvement for !{cmd_name}: {s.get('type')} ({int(s['confidence']*100)}% confidence)")
                        
                        # Apply to all guilds using this command
                        data_dir = "data"
                        if os.path.exists(data_dir):
                            for filename in os.listdir(data_dir):
                                if filename.startswith("guild_") and filename.endswith(".json"):
                                    try:
                                        guild_id = filename[6:-5]
                                        custom_cmds = dm.get_guild_data(int(guild_id), "custom_commands", {})
                                        
                                        if cmd_name in custom_cmds:
                                            cmd_data = custom_cmds[cmd_name]
                                            try:
                                                cmd_dict = json.loads(cmd_data) if isinstance(cmd_data, str) else cmd_data
                                                if isinstance(cmd_dict, dict):
                                                    if "improvements" not in cmd_dict:
                                                        cmd_dict["improvements"] = []
                                                    
                                                    # Add error prevention based on error type
                                                    prevention = self._generate_prevention(error_type, cmd_name)
                                                    if prevention:
                                                        cmd_dict["error_prevention"] = prevention
                                                    
                                                    cmd_dict["improvements"].append({
                                                        "type": suggestion_type,
                                                        "suggestion": suggestion_text,
                                                        "confidence": s.get("confidence"),
                                                        "applied_at": time.time(),
                                                        "auto_applied": True,
                                                        "prevention_added": bool(prevention)
                                                    })
                                                    custom_cmds[cmd_name] = json.dumps(cmd_dict)
                                                    dm.update_guild_data(int(guild_id), "custom_commands", custom_cmds)
                                            except:
                                                new_doc = {"original": cmd_data, "improvements": [{"type": suggestion_type, "suggestion": suggestion_text, "auto_applied": True, "applied_at": time.time()}]}
                                                if error_type:
                                                    new_doc["error_prevention"] = self._generate_prevention(error_type, cmd_name)
                                                custom_cmds[cmd_name] = json.dumps(new_doc)
                                                dm.update_guild_data(int(guild_id), "custom_commands", custom_cmds)
                                    except Exception as e:
                                        logger.error(f"Error applying suggestion to guild {filename}: {e}")
                        
                        # Record auto-applied suggestion
                        auto_log = dm.load_json("auto_applied_improvements", default=[])
                        if not isinstance(auto_log, list):
                            auto_log = []
                        auto_log.append({
                            "command": cmd_name,
                            "type": suggestion_type,
                            "error_type": error_type,
                            "suggestion": suggestion_text,
                            "confidence": s.get("confidence"),
                            "applied_at": time.time()
                        })
                        dm.save_json("auto_applied_improvements", auto_log)
                        
                        # Notify all guilds with improved commands
                        await self._notify_command_improvements(cmd_name, suggestion_text, suggestion_type, s.get("confidence"))
                        
                    except Exception as e:
                        logger.error(f"Error auto-applying suggestion: {e}")
                
                # Save remaining lower confidence suggestions for potential manual review
                if for_review:
                    dm.save_json("command_improvement_suggestions", {
                        "timestamp": time.time(),
                        "suggestions": for_review[:10]
                    })
                    logger.info("Stored %d lower-confidence suggestions for review", len(for_review))
                else:
                    # Clear old suggestions if all were auto-applied
                    dm.save_json("command_improvement_suggestions", {
                        "timestamp": time.time(),
                        "suggestions": []
                    })
                    
                logger.info(f"Auto-applied {len(auto_apply)} command improvements")
                
            else:
                logger.info("No command improvement suggestions generated")
                
        except Exception as e:
            logger.error("Error analyzing command usage: %s", e)
    
    def _generate_error_suggestion(self, cmd_name: str, error_type: str) -> str:
        """Generate a specific suggestion based on error type."""
        if error_type == "missing_argument":
            return f"Users often forget required arguments when using !{cmd_name}. Consider adding argument prompts or making arguments optional with defaults."
        elif error_type == "not_found":
            return f"Users often reference non-existent items with !{cmd_name}. Consider adding a list command or autocomplete suggestions."
        elif error_type == "permission":
            return f"Users often lack permissions for !{cmd_name}. Consider adding permission checks with helpful error messages."
        else:
            return f"Users frequently encounter errors with !{cmd_name}. Review command documentation and error handling."
    
    def _generate_prevention(self, error_type: str, cmd_name: str) -> dict:
        """Generate automatic prevention for common error types."""
        if not error_type:
            return None
        
        prevention = {
            "error_type": error_type,
            "prevention_enabled": True
        }
        
        if error_type == "missing_argument":
            prevention["action"] = "prompt_missing_args"
            prevention["message"] = f"Missing required argument! Usage: !{cmd_name} <required_arg> [optional_args]"
            prevention["auto_suggest"] = True
        
        elif error_type == "not_found":
            prevention["action"] = "suggest_alternatives"
            prevention["message"] = f"Item not found. Try: !{cmd_name} list to see available options."
            prevention["show_similar"] = True
        
        elif error_type == "permission":
            prevention["action"] = "clear_permission_guide"
            prevention["message"] = f"You need permission to use !{cmd_name}. Contact an admin for access."
            prevention["show_required_role"] = True
        
        else:
            prevention["action"] = "enhanced_error_message"
            prevention["message"] = f"Error using !{cmd_name}. Check: !help {cmd_name}"
            prevention["show_usage"] = True
        
        return prevention
    
    async def _notify_command_improvements(self, cmd_name: str, suggestion: str, suggestion_type: str, confidence: float):
        """Notify guilds when a command has been auto-improved."""
        import datetime
        conf_pct = int(confidence * 100)
        type_label = suggestion_type.replace("_", " ").title()
        
        embed = discord.Embed(
            title=f"⚡ Command Auto-Improved: !{cmd_name}",
            description=f"The AI has automatically improved this command based on usage patterns.",
            color=discord.Color.green()
        )
        embed.add_field(name="Improvement Type", value=type_label, inline=True)
        embed.add_field(name="Confidence", value=f"{conf_pct}%", inline=True)
        embed.add_field(name="Change Applied", value=suggestion, inline=False)
        embed.timestamp = datetime.datetime.now()
        embed.set_footer(text="Adaptive Command Refinement • AI Self-Improvement")
        
        # Find all guilds that have this command and notify
        data_dir = "data"
        notified_guilds = []
        
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_id = int(filename[6:-5])
                        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
                        
                        if cmd_name in custom_cmds:
                            guild = self.get_guild(guild_id)
                            if guild:
                                log_channel_id = dm.get_guild_data(guild_id, "log_channel")
                                if log_channel_id:
                                    channel = guild.get_channel(log_channel_id)
                                    if channel:
                                        await channel.send(embed=embed)
                                        notified_guilds.append(guild.name)
                    except Exception as e:
                        logger.error(f"Error notifying guild {filename}: {e}")
        
        if notified_guilds:
            logger.info(f"Notified {len(notified_guilds)} guilds about !{cmd_name} improvement")

# Initialize Bot
bot = ImmortalBot()

class AIReplyModal(ui.Modal, title='Reply to AI'):
    """Modal for users to answer AI clarifying questions."""
    def __init__(self, question: str):
        super().__init__()
        self.add_item(ui.TextInput(
            label=question,
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer here..."
        ))
    
    answer: ui.TextInput

# --- Slash Commands ---

@bot.tree.command(name="bot", description="AI-powered server management")
@app_commands.describe(text="What do you want me to do?")
async def slash_bot(interaction: discord.Interaction, text: str):
    """The main AI portal with multi-step conversation support."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only Administrators can use AI commands.", ephemeral=True)
        return

    now = datetime.datetime.now().timestamp()
    last_use = bot._bot_cooldowns.get(interaction.user.id, 0)
    remaining = bot._bot_cooldown_seconds - (now - last_use)
    if remaining > 0:
        return await interaction.response.send_message(
            f"Please wait {int(remaining)}s before using /bot again.",
            ephemeral=True
        )
    bot._bot_cooldowns[interaction.user.id] = now

    await interaction.response.defer(ephemeral=True)
    
    try:
        await _process_ai_turn(interaction, text)
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)

async def _process_ai_turn(interaction: discord.Interaction, user_input: str):
    """Process a single turn of the AI conversation."""
    guild_id = interaction.guild.id
    user_id = interaction.user.id
    
    # Retrieve relevant memories for context
    relevant_memories = vector_memory.retrieve_relevant_conversations(
        guild_id=guild_id,
        user_id=user_id,
        query=user_input,
        n_results=3
    )
    
    # Add memory context to the system prompt if we have relevant memories
    memory_context = ""
    if relevant_memories:
        memory_context = "\n\nRELEVANT PAST CONVERSATIONS:\n"
        for i, mem in enumerate(relevant_memories, 1):
            memory_context += f"\n{i}. Similar conversation (similarity: {mem['similarity']:.2f}):\n{mem['document'][:500]}...\n"
    
    res = await bot.ai.chat(guild_id, user_id, user_input, SYSTEM_PROMPT + memory_context)
    
    reasoning = res.get("reasoning", "Thinking...")
    walkthrough = res.get("walkthrough", "Planning...")
    summary = res.get("summary", "Ready to proceed.")
    needs_input = res.get("needs_input", False)
    question = res.get("question", "")
    
    if needs_input and question:
        bot.ai_sessions[user_id] = {
            "messages": [{"role": "assistant", "content": json.dumps(res)}],
            "last_interaction": interaction,
            "original_request": user_input,
            "question": question,
            "expires_at": time.time() + 300
        }
        
        embed = discord.Embed(
            title="AI Needs More Info",
            description=f"**Reasoning:**\n{reasoning}\n\n**Question:**\n{question}",
            color=discord.Color.orange()
        )
        
        view = discord.ui.View()
        reply_btn = discord.ui.Button(label="Reply", style=discord.ButtonStyle.primary, custom_id="ai_reply")
        skip_btn = discord.ui.Button(label="Skip / Use Defaults", style=discord.ButtonStyle.secondary, custom_id="ai_skip")
        
        async def reply_callback(it: discord.Interaction):
            if it.user.id != user_id:
                return await it.response.send_message("Only the user who started this can reply.", ephemeral=True)
            
            modal = AIReplyModal(question=question)
            await it.response.send_modal(modal)
            
            async def on_submit_wrapper(modal_it: discord.Interaction):
                answer = modal.answer.value
                
                if user_id in bot.ai_sessions:
                    del bot.ai_sessions[user_id]
                
                await modal_it.response.send_message("🔄 Processing your answer...", ephemeral=True)
                await _process_ai_turn(modal_it, f"[User answered your question]: {answer}")
            
            modal.on_submit = on_submit_wrapper
        
        async def skip_callback(it: discord.Interaction):
            if it.user.id != user_id:
                return await it.response.send_message("Only the user who started this can skip.", ephemeral=True)
            
            if user_id in bot.ai_sessions:
                del bot.ai_sessions[user_id]
            
            await it.response.edit_message(content="🔄 Proceeding with defaults...", embed=None, view=None)
            await _process_ai_turn(it, "[User said to use defaults]")
        
        reply_btn.callback = reply_callback
        skip_btn.callback = skip_callback
        view.add_item(reply_btn)
        view.add_item(skip_btn)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        actions = res.get("actions", [])
        
        if not actions:
            embed = discord.Embed(title="AI Response", description=summary, color=discord.Color.blue())
            await interaction.followup.send(embed=embed, ephemeral=True)
            history_manager.add_exchange(guild_id, user_id, user_input, summary)
            # Store in vector memory for long-term recall
            vector_memory.store_conversation(
                guild_id=guild_id,
                user_id=user_id,
                user_message=user_input,
                bot_response=summary,
                reasoning=reasoning,
                walkthrough=walkthrough
            )
            # Self-reflection mechanism (opt-in via SELF_REFLECT_ENABLED env var)
            if os.getenv("SELF_REFLECT_ENABLED", "false").lower() == "true":
                await _self_reflect_on_response(guild_id, user_id, user_input, summary, reasoning, walkthrough)
            return
        
        bot.pending_confirms[user_id] = {
            "actions": actions,
            "summary": summary,
            "interaction": interaction
        }
        
        embed = discord.Embed(title="AI Reasoning & Plan", description=f"**Reasoning:**\n{reasoning}\n\n**Walkthrough:**\n{walkthrough}", color=discord.Color.blue())
        
        view = discord.ui.View()
        proceed_btn = discord.ui.Button(label="Proceed", style=discord.ButtonStyle.success, custom_id="proceed")
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel")
        
        async def proceed_callback(it: discord.Interaction):
            if it.user.id != user_id:
                return await it.response.send_message("Only the user who started this can proceed.", ephemeral=True)
            
            await it.response.edit_message(content="🔄 Execution in progress...", embed=None, view=None)
            
            from actions import ActionHandler
            handler = ActionHandler(bot)
            result = await handler.execute_sequence(it, bot.pending_confirms[user_id]["actions"])
            
            summary_text = "\n".join([f"{'✅' if s else '❌'} {n}" for n, s in result["results"]])
            
            if result["success"]:
                final_msg = f"**Execution Summary:**\n{summary_text}\n\n{bot.pending_confirms[user_id]['summary']}"
            else:
                rollback_text = ""
                if result["rolled_back"]:
                    rb = "\n".join([f"{'✅' if s else '⚠️'} {n}" for n, s in result["rolled_back"]])
                    rollback_text = f"\n\n**Auto-Rollback ({len(result['rolled_back'])} actions):**\n{rb}"
                final_msg = f"**Failed at step {result['failed_at'] + 1}: `{result['failed_action']}`**\nError: {result['error']}\n\n**Executed:**\n{summary_text}{rollback_text}"
            
            await it.followup.send(final_msg, ephemeral=True)
            history_manager.add_exchange(guild_id, user_id, user_input, summary)
            # Store in vector memory for long-term recall
            vector_memory.store_conversation(
                guild_id=guild_id,
                user_id=user_id,
                user_message=user_input,
                bot_response=summary,
                reasoning=reasoning,
                walkthrough=walkthrough
            )
            # Self-reflection (opt-in)
            if os.getenv("SELF_REFLECT_ENABLED", "false").lower() == "true":
                await _self_reflect_on_response(guild_id, user_id, user_input, summary, reasoning, walkthrough)
            
            del bot.pending_confirms[user_id]
        
        async def cancel_callback(it: discord.Interaction):
            if it.user.id != user_id:
                return await it.response.send_message("Only the user who started this can cancel.", ephemeral=True)
            await it.response.edit_message(content="❌ Action cancelled.", embed=None, view=None)
            del bot.pending_confirms[user_id]
        
        proceed_btn.callback = proceed_callback
        cancel_btn.callback = cancel_callback
        view.add_item(proceed_btn)
        view.add_item(cancel_btn)
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

# --- Utility Commands ---

@bot.tree.command(name="status", description="View bot and system health")
async def status_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    
    vm_stats = vector_memory.get_memory_stats()
    guild_api = dm.get_guild_api_key(guild.id)
    
    embed = discord.Embed(title="System Status", color=discord.Color.green())
    embed.add_field(name="Bot", value="🟢 Online", inline=True)
    embed.add_field(name="Guild", value=f"🟢 {guild.name}", inline=True)
    embed.add_field(name="Vector Memory", value=f"{vm_stats.get('count', 0)} memories stored", inline=False)
    embed.add_field(name="AI Provider", value=(guild_api or {}).get("provider", bot.ai.default_provider).title(), inline=True)
    embed.add_field(name="AI Model", value=bot.ai.model, inline=True)
    embed.add_field(name="API Key", value="🟢 Server-specific" if guild_api else "🔴 Default", inline=True)
    embed.add_field(name="Self-Reflection", value="🔴 Disabled" if os.getenv("SELF_REFLECT_ENABLED", "false").lower() != "true" else "🟢 Enabled", inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="List all commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="Immortal Bot Help", color=discord.Color.blue())
    embed.add_field(name="/bot <text>", value="AI-powered management.", inline=False)
    embed.add_field(name="/status", value="System health check.", inline=False)
    embed.add_field(name="/list", value="Show active automations.", inline=False)
    embed.add_field(name="/config", value="Adjust bot settings.", inline=False)
    embed.add_field(name="/cancel", value="Abort pending AI action.", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="cancel", description="Aborts current running action or last pending confirmation")
async def cancel_cmd(interaction: discord.Interaction):
    if interaction.user.id in bot.pending_confirms:
        del bot.pending_confirms[interaction.user.id]
        await interaction.response.send_message("Pending action cancelled.", ephemeral=True)
    else:
        await interaction.response.send_message("No pending action to cancel.", ephemeral=True)

@bot.tree.command(name="suggest", description="Submit a suggestion for the server")
@app_commands.describe(title="Suggestion title", description="Describe your suggestion")
async def suggest_cmd(interaction: discord.Interaction, title: str, description: str):
    guild = interaction.guild
    suggestions_channel_id = dm.get_guild_data(guild.id, "suggestions_channel")
    
    if not suggestions_channel_id:
        await interaction.response.send_message("No suggestions channel set up yet!", ephemeral=True)
        return
    
    channel = guild.get_channel(suggestions_channel_id)
    if not channel:
        await interaction.response.send_message("Suggestions channel not found!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"💡 {title}",
        description=description,
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Suggested by {interaction.user}")
    embed.add_field(name="Status", value="⏳ Pending Review", inline=False)
    
    message = await channel.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")
    
    await interaction.response.send_message("✅ Suggestion submitted!", ephemeral=True)

@bot.tree.command(name="list", description="Shows all active automations")
async def list_cmd(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
    triggers = dm.get_guild_data(guild_id, "trigger_roles", {})
    
    embed = discord.Embed(title="Active Automations", color=discord.Color.teal())
    embed.add_field(name="Custom Commands", value=f"{len(custom_cmds)} active" or "None")
    embed.add_field(name="Trigger Roles", value=f"{len(triggers)} active" or "None")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="config", description="Change AI provider, model, keys, etc.")
async def config_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
        
    embed = discord.Embed(title="Bot Configuration", description="Use subcommands to adjust settings.", color=discord.Color.dark_grey())
    embed.add_field(name="/config model <name>", value="Set AI model (e.g. gpt-4, claude-3)", inline=False)
    embed.add_field(name="/config provider <name>", value="Set AI provider (openrouter, openai, gemini)", inline=False)
    embed.add_field(name="/config apikey <key>", value="Set server-specific API key", inline=False)
    embed.add_field(name="/config prefix <char>", value="Set server prefix", inline=False)
    embed.add_field(name="/config depth <number>", value="Set memory depth", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="config_model", description="Set the AI model")
@app_commands.describe(model="Model name (e.g. gpt-4, claude-3)")
async def config_model(interaction: discord.Interaction, model: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    
    dm.update_guild_data(interaction.guild.id, "custom_model", model)
    bot.ai.model = model
    await interaction.response.send_message(f"AI model set to **{model}** for this server.", ephemeral=True)

@bot.tree.command(name="config_provider", description="Set the AI provider")
@app_commands.choices(provider=[
    app_commands.Choice(name="OpenRouter", value="openrouter"),
    app_commands.Choice(name="OpenAI", value="openai"),
    app_commands.Choice(name="Gemini", value="gemini"),
])
async def config_provider(interaction: discord.Interaction, provider: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    if provider not in bot.ai.base_urls:
        return await interaction.response.send_message(f"Unknown provider. Valid: {', '.join(bot.ai.base_urls.keys())}", ephemeral=True)
    
    current_key = dm.get_guild_api_key(interaction.guild.id)
    api_key = current_key.get("api_key") if current_key else os.getenv("AI_API_KEY", "")
    dm.set_guild_api_key(interaction.guild.id, api_key, provider)
    await interaction.response.send_message(f"AI provider set to **{provider}** for this server.", ephemeral=True)

@bot.tree.command(name="config_prefix", description="Set the server prefix")
@app_commands.describe(prefix="New prefix character")
async def config_prefix(interaction: discord.Interaction, prefix: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    if len(prefix) > 5:
        return await interaction.response.send_message("Prefix must be 5 characters or less.", ephemeral=True)
    dm.update_guild_data(interaction.guild.id, "prefix", prefix)
    await interaction.response.send_message(f"Server prefix set to **{prefix}**.", ephemeral=True)

@bot.tree.command(name="config_depth", description="Set memory depth")
@app_commands.describe(depth="Number of messages to remember")
async def config_depth(interaction: discord.Interaction, depth: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    if depth < 5 or depth > 100:
        return await interaction.response.send_message("Depth must be between 5 and 100.", ephemeral=True)
    dm.update_guild_data(interaction.guild.id, "memory_depth", depth)
    await interaction.response.send_message(f"Memory depth set to **{depth}**.", ephemeral=True)

@bot.tree.command(name="config_apikey", description="Set server-specific AI API key")
@app_commands.describe(api_key="Your API key", provider="AI provider (default: openrouter)")
@app_commands.choices(provider=[
    app_commands.Choice(name="OpenRouter", value="openrouter"),
    app_commands.Choice(name="OpenAI", value="openai"),
    app_commands.Choice(name="Gemini", value="gemini"),
])
async def config_apikey(interaction: discord.Interaction, api_key: str, provider: str = "openrouter"):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    
    dm.set_guild_api_key(interaction.guild.id, api_key, provider)
    await interaction.response.send_message(f"✅ API key set for this server!\nProvider: **{provider}**", ephemeral=True)

@bot.tree.command(name="undo", description="Reverse latest actions")
@app_commands.describe(count="Number of action groups to undo (default: 1)")
async def undo_cmd(interaction: discord.Interaction, count: int = 1):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    
    if count < 1 or count > 10:
        return await interaction.response.send_message("Count must be between 1 and 10.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    from actions import ActionHandler
    handler = ActionHandler(bot)
    results = await handler.undo_last_actions(interaction, count)
    
    summary = "\n".join([f"{'✅' if s else '❌'} {n}" for n, s in results])
    await interaction.followup.send(f"**Undo Summary:**\n{summary}", ephemeral=True)

@bot.tree.command(name="health", description="View community health report")
async def health_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    
    report = await bot.community_health.generate_health_report(interaction.guild.id)
    
    if "error" in report:
        await interaction.followup.send("Unable to generate report. Not enough data yet.")
        return
    
    health = report.get("health_score", 0)
    color = discord.Color.green() if health > 0.6 else discord.Color.orange() if health > 0.4 else discord.Color.red()
    
    embed = discord.Embed(
        title="📊 Community Health",
        description=f"Health Score: **{health:.1f}/10**",
        color=color
    )
    embed.add_field(
        name="Members",
        value=f"Total: {report.get('member_count', 0)} | Active: {report.get('active_members', 0)} | Isolated: {report.get('isolated_members', 0)}",
        inline=False
    )
    embed.add_field(name="Clusters", value=f"{len(report.get('clusters', []))} active groups", inline=True)
    embed.timestamp = datetime.now()
    embed.set_footer(text="Community Health Analysis")
    
    await interaction.followup.send(embed=embed)

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")
    await bot.auto_setup.on_guild_join(guild)

@bot.event
async def on_guild_remove(guild: discord.Guild):
    logger.info(f"Left guild: {guild.name} (ID: {guild.id})")
    await bot.auto_setup.on_guild_remove(guild)

@bot.event  
async def on_member_remove(member):
    """Handle exit interviews when staff leave"""
    try:
        await bot.staff_extras.on_member_remove(member)
    except Exception as e:
        logger.warning(f"Exit interview error: {e}")

@bot.event
async def on_raw_reaction_add(payload):
    """Handle reaction add for exit interviews"""
    try:
        channel = bot.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        user = payload.member
        if user and not user.bot:
            await bot.staff_extras.on_reaction_add(message, user)
    except Exception as e:
        logger.warning(f"Reaction add error: {e}")

# Main Execution
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.critical("DISCORD_TOKEN not found in environment or .env file.")
        logger.critical("Please copy .env.example to .env and add your bot token.")
        exit(1)
    
    ai_key = os.getenv("AI_API_KEY")
    if not ai_key:
        logger.warning("AI_API_KEY not found. The /bot command will not work.")
    
    bot.run(token)
