import os
import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import datetime
import random
import signal
from dotenv import load_dotenv
from data_manager import dm
from history_manager import history_manager
from ai_client import AIClient, SYSTEM_PROMPT
from logger import logger
from task_scheduler import TaskScheduler

# Import Modules
from modules.economy import Economy
from modules.leveling import Leveling
from modules.staff_system import StaffSystem
from modules.appeals import Appeals
from modules.trigger_roles import TriggerRoles

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
        
        # Internal Systems
        self.economy = Economy(self)
        self.leveling = Leveling(self)
        self.appeals = Appeals(self)
        self.trigger_roles = TriggerRoles(self)
        self.scheduler = TaskScheduler(self)

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
        self._setup_crash_recovery()
        self._setup_signal_handlers()

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

    async def on_message(self, message):
        if message.author.bot:
            return

        # 1. Passive Systems (XP & Triggers)
        await self.leveling.handle_message(message)
        await self.trigger_roles.handle_message(message)

        # 2. Prefix Commands
        prefix = await self.get_dynamic_prefix(self, message)
        if message.content.startswith(prefix):
            parts = message.content[len(prefix):].split()
            if not parts: return
            cmd_name = parts[0]
            
            guild_cmds = dm.get_guild_data(message.guild.id, "custom_commands", {})
            if cmd_name in guild_cmds:
                self._track_command_usage(message.guild.id, cmd_name)
                from actions import ActionHandler
                handler = ActionHandler(self)
                await handler.execute_custom_command(message, guild_cmds[cmd_name])
                return

        await self.process_commands(message)

    def _track_command_usage(self, guild_id: int, cmd_name: str):
        """Track ! command usage for AI feedback loop."""
        usage = dm.get_guild_data(guild_id, "command_usage", {})
        if cmd_name not in usage:
            usage[cmd_name] = {"count": 0, "last_used": 0, "users": set()}
        usage[cmd_name]["count"] = usage[cmd_name].get("count", 0) + 1
        import time
        usage[cmd_name]["last_used"] = time.time()
        users = usage[cmd_name].get("users", [])
        if not isinstance(users, list):
            users = []
        users.append(cmd_name)
        usage[cmd_name]["users"] = users[-100:]
        dm.update_guild_data(guild_id, "command_usage", usage)

# Initialize Bot
bot = ImmortalBot()

# --- Slash Commands ---

@bot.tree.command(name="bot", description="AI-powered server management")
@app_commands.describe(text="What do you want me to do?")
async def slash_bot(interaction: discord.Interaction, text: str):
    """The main AI portal."""
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
        # 1. Reasoning & Walkthrough
        res = await bot.ai.chat(interaction.guild.id, interaction.user.id, text, SYSTEM_PROMPT)
        
        reasoning = res.get("reasoning", "Thinking...")
        walkthrough = res.get("walkthrough", "Planning...")
        summary = res.get("summary", "Ready to proceed.")

        # Store for confirmation
        bot.pending_confirms[interaction.user.id] = {
            "actions": res.get("actions", []),
            "summary": summary,
            "interaction": interaction
        }

        # Build Embed with Buttons
        embed = discord.Embed(title="AI Reasoning & Plan", description=f"**Reasoning:**\n{reasoning}\n\n**Walkthrough:**\n{walkthrough}", color=discord.Color.blue())
        
        view = discord.ui.View()
        proceed_btn = discord.ui.Button(label="Proceed", style=discord.ButtonStyle.success, custom_id="proceed")
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel")
        
        async def proceed_callback(it: discord.Interaction):
            if it.user.id != interaction.user.id:
                return await it.response.send_message("This isn't your interaction.", ephemeral=True)
            
            await it.response.edit_message(content="🔄 Execution in progress...", embed=None, view=None)
            
            from actions import ActionHandler
            handler = ActionHandler(bot)
            result = await handler.execute_sequence(interaction, bot.pending_confirms[interaction.user.id]["actions"])
            
            summary_text = "\n".join([f"{'✅' if s else '❌'} {n}" for n, s in result["results"]])
            
            if result["success"]:
                final_msg = f"**Execution Summary:**\n{summary_text}\n\n{bot.pending_confirms[interaction.user.id]['summary']}"
            else:
                rollback_text = ""
                if result["rolled_back"]:
                    rb = "\n".join([f"{'✅' if s else '⚠️'} {n}" for n, s in result["rolled_back"]])
                    rollback_text = f"\n\n**Auto-Rollback ({len(result['rolled_back'])} actions):**\n{rb}"
                final_msg = f"**Failed at step {result['failed_at'] + 1}: `{result['failed_action']}`**\nError: {result['error']}\n\n**Executed:**\n{summary_text}{rollback_text}"
            
            await it.followup.send(final_msg, ephemeral=True)
            
            history_manager.add_exchange(interaction.guild.id, interaction.user.id, text, summary)
            
            del bot.pending_confirms[interaction.user.id]

        async def cancel_callback(it: discord.Interaction):
            await it.response.edit_message(content="❌ Action cancelled.", embed=None, view=None)
            del bot.pending_confirms[interaction.user.id]

        proceed_btn.callback = proceed_callback
        cancel_btn.callback = cancel_callback
        view.add_item(proceed_btn)
        view.add_item(cancel_btn)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}", ephemeral=True)

# --- Utility Commands ---

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
    embed.add_field(name="/config prefix <char>", value="Set server prefix", inline=False)
    embed.add_field(name="/config depth <number>", value="Set memory depth", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="config_model", description="Set the AI model")
@app_commands.describe(model="Model name (e.g. gpt-4, claude-3)")
async def config_model(interaction: discord.Interaction, model: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Admin only.", ephemeral=True)
    bot.ai.model = model
    await interaction.response.send_message(f"AI model set to **{model}**.", ephemeral=True)

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
    bot.ai.provider = provider
    await interaction.response.send_message(f"AI provider set to **{provider}**.", ephemeral=True)

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
