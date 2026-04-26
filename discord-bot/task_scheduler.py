import asyncio
import time
from croniter import croniter
from datetime import datetime
import discord
from data_manager import dm
from logger import logger

import os
import sys

# Platform-specific file locking
if sys.platform != 'win32':
    import fcntl

class TaskScheduler:
    """Background task scheduler using cron expressions."""
    
    def __init__(self, bot):
        self.bot = bot
        self._tasks = {}
        self._running = False
        self._ai_tasks = {}
        self._lock_file = None

    async def start(self):
        """Start the scheduler loop."""
        self._running = True
        self._lock_file = open(os.path.join(os.path.dirname(__file__), ".task_lock"), "w")
        self._load_scheduled_tasks()
        logger.info("Task scheduler started with %d tasks", len(self._tasks))
        asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        for name, task in self._tasks.items():
            if task and not task.done():
                task.cancel()
        if self._lock_file:
            self._lock_file.close()
        logger.info("Task scheduler stopped")

    def _load_scheduled_tasks(self):
        """Load scheduled tasks from data store."""
        tasks = dm.load_json("scheduled_tasks", default={})
        for name, task_data in tasks.items():
            self._tasks[name] = None

    def add_task(self, name: str, cron_expr: str, handler=None, guild_id: int = None, params: dict = None):
        """Register a new scheduled task."""
        tasks = dm.load_json("scheduled_tasks", default={})
        # Preserve last_run if it exists
        last_run = tasks.get(name, {}).get("last_run")

        tasks[name] = {
            "cron": cron_expr,
            "guild_id": guild_id,
            "params": params or {},
            "enabled": True,
            "last_run": last_run
        }
        dm.save_json("scheduled_tasks", tasks)
        self._tasks[name] = handler
        logger.info("Scheduled task added: %s (cron: %s)", name, cron_expr)

    def remove_task(self, name: str):
        """Remove a scheduled task."""
        tasks = dm.load_json("scheduled_tasks", default={})
        if name in tasks:
            del tasks[name]
            dm.save_json("scheduled_tasks", tasks)
            if name in self._tasks:
                self._tasks.pop(name, None)
            logger.info("Scheduled task removed: %s", name)

    def _acquire_lock(self) -> bool:
        """Acquire file lock to prevent race conditions."""
        if sys.platform == 'win32':
            # Windows doesn't support fcntl, skip locking
            return True
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _release_lock(self):
        """Release file lock."""
        if sys.platform == 'win32':
            return
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

    async def _scheduler_loop(self):
        """Main scheduler loop - checks every 30 seconds."""
        while self._running:
            try:
                await self._check_tasks()
                await self._check_ai_tasks()
            except Exception as e:
                logger.error("Scheduler loop error: %s", e)
            await asyncio.sleep(30)

    async def _check_tasks(self):
        """Check and execute due tasks."""
        if not self._acquire_lock():
            return
        try:
            tasks = dm.load_json("scheduled_tasks", default={})
            now = datetime.now()
        
            for name, task_data in tasks.items():
                if not task_data.get("enabled", True):
                    continue
                
                cron_expr = task_data.get("cron")
                if not cron_expr:
                    continue
                
                try:
                    cron = croniter(cron_expr, now)
                    prev_run = cron.get_prev(datetime)
                    
                    last_run = task_data.get("last_run")
                    if last_run is None or prev_run.timestamp() > last_run:
                        logger.info("Executing scheduled task: %s", name)
                        await self._execute_task(name, task_data)
                        task_data["last_run"] = prev_run.timestamp()
                        tasks[name] = task_data
                        dm.save_json("scheduled_tasks", tasks)
                except Exception as e:
                    logger.error("Task %s cron error: %s", name, e)
        finally:
            self._release_lock()

    async def _execute_task(self, name: str, task_data: dict):
        """Execute a single scheduled task."""
        try:
            guild_id = task_data.get("guild_id")
            params = task_data.get("params", {})
            
            # Use registered handler if available
            handler = self._tasks.get(name)
            if handler:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(name, params)
                    else:
                        handler(name, params)
                    return
                except Exception as e:
                    logger.error(f"Error in task handler for {name}: {e}")

            if name == "daily_backup":
                dm.backup_data()
                logger.info("Daily backup completed via scheduler")
            elif name == "cleanup_old_data":
                days = params.get("days", 30)
                await dm.cleanup_old_data(days)
                logger.info("Data cleanup completed (%d days)", days)
            elif name == "weekly_leaderboard":
                if guild_id:
                    await self._post_leaderboard(guild_id)
            else:
                logger.warning("Unknown scheduled task: %s", name)
        except Exception as e:
            logger.error("Task %s execution error: %s", name, e)

    async def _execute_ai_task(self, name: str, task_data: dict):
        """Execute an AI-scheduled action."""
        try:
            guild_id = task_data.get("guild_id")
            action_type = task_data.get("action_type")
            action_params = task_data.get("action_params", {})
            channel_id = task_data.get("channel_id")
            
            guild = self.bot.get_guild(guild_id)
            if not guild:
                logger.error("Guild %d not found for AI task: %s", guild_id, name)
                return
            
            if action_type == "announcement":
                channel = guild.get_channel(channel_id)
                if channel:
                    embed = discord.Embed(
                        title=action_params.get("title", "Scheduled Announcement"),
                        description=action_params.get("message", ""),
                        color=discord.Color.blue()
                    )
                    await channel.send(embed=embed)
                    
            elif action_type == "reminder":
                user_id = action_params.get("user_id")
                user = guild.get_member(user_id)
                if user:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await channel.send(f"📢 {user.mention} {action_params.get('message', '')}")
                        
            elif action_type == "ai_action":
                ai_input = action_params.get("ai_input")
                if ai_input and hasattr(self.bot, 'ai'):
                    from ai_client import AIClient, SYSTEM_PROMPT
                    response = await self.bot.ai.chat(guild_id, self.bot.user.id, ai_input, SYSTEM_PROMPT)
                    if response.get("action"):
                        from actions import ActionHandler
                        handler = ActionHandler(self.bot)
                        await handler.execute_action(response, guild, interaction=None)
            
            logger.info("AI scheduled task executed: %s", name)
            
        except Exception as e:
            logger.error("AI task %s execution error: %s", name, e)

    def add_ai_task(self, name: str, guild_id: int, cron_expr: str, action_type: str, action_params: dict, channel_id: int = None):
        """Register an AI-scheduled action task."""
        tasks = dm.load_json("ai_scheduled_tasks", default={})
        # Preserve last_run if it exists
        last_run = tasks.get(name, {}).get("last_run")

        tasks[name] = {
            "cron": cron_expr,
            "guild_id": guild_id,
            "action_type": action_type,
            "action_params": action_params,
            "channel_id": channel_id,
            "enabled": True,
            "last_run": last_run
        }
        dm.save_json("ai_scheduled_tasks", tasks)
        logger.info("AI scheduled task added: %s (%s)", name, action_type)

    def remove_ai_task(self, name: str):
        """Remove an AI-scheduled task."""
        tasks = dm.load_json("ai_scheduled_tasks", default={})
        if name in tasks:
            del tasks[name]
            dm.save_json("ai_scheduled_tasks", tasks)
            logger.info("AI scheduled task removed: %s", name)

    async def _check_ai_tasks(self):
        """Check and execute due AI-scheduled tasks."""
        tasks = dm.load_json("ai_scheduled_tasks", default={})
        now = datetime.now()
        
        for name, task_data in tasks.items():
            if not task_data.get("enabled", True):
                continue
            
            cron_expr = task_data.get("cron")
            if not cron_expr:
                continue
            
            try:
                cron = croniter(cron_expr, now)
                prev_run = cron.get_prev(datetime)
                
                last_run = task_data.get("last_run")
                if last_run is None or prev_run.timestamp() > last_run:
                    logger.info("Executing AI scheduled task: %s", name)
                    await self._execute_ai_task(name, task_data)
                    task_data["last_run"] = prev_run.timestamp()
                    tasks[name] = task_data
                    dm.save_json("ai_scheduled_tasks", tasks)
            except Exception as e:
                logger.error("AI task %s cron error: %s", name, e)

    async def _post_leaderboard(self, guild_id: int):
        """Post weekly leaderboard to the guild."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        sorted_users = sorted(xp_data.items(), key=lambda x: x[1], reverse=True)[:10]
        
        if not sorted_users:
            return
        
        lines = []
        for i, (user_id_str, xp) in enumerate(sorted_users, 1):
            member = guild.get_member(int(user_id_str))
            name = member.display_name if member else f"User {user_id_str}"
            lines.append(f"{i}. **{name}** - {xp} XP")
        
        embed = discord.Embed(
            title="Weekly Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        
        channel_id = dm.get_guild_data(guild_id, "leaderboard_channel")
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.send(embed=embed)
