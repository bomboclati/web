import os
import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import datetime
import random
from dotenv import load_dotenv
from data_manager import dm
from history_manager import history_manager
from ai_client import AIClient, SYSTEM_PROMPT

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
        
        # Internal Systems
        self.economy = Economy(self)
        self.leveling = Leveling(self)
        self.appeals = Appeals(self)
        self.trigger_roles = TriggerRoles(self)

    async def get_dynamic_prefix(self, bot, message):
        if not message.guild:
            return "!"
        return dm.get_guild_data(message.guild.id, "prefix", "!")

    async def setup_hook(self):
        print("Recovering immortal state...")
        # Load all guild data and recover state
        # In a real scenario, loop through all guild files
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id}) (IMMORTAL)")

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
                from actions import ActionHandler
                handler = ActionHandler(self)
                await handler.execute_custom_command(message, guild_cmds[cmd_name])
                return

        await self.process_commands(message)

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
            
            # Start Execution
            await it.response.edit_message(content="🔄 Execution in progress...", embed=None, view=None)
            
            from actions import ActionHandler
            handler = ActionHandler(bot)
            results = await handler.execute_sequence(interaction, bot.pending_confirms[interaction.user.id]["actions"])
            
            # Final Summary
            summary_text = "\n".join([f"{'✅' if s else '❌'} {n}" for n, s in results])
            await it.followup.send(f"**Execution Summary:**\n{summary_text}\n\n{bot.pending_confirms[interaction.user.id]['summary']}", ephemeral=True)
            
            # Record in history
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
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="undo", description="Reverse latest actions")
async def undo_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("Undo logic pending implementation history storage.", ephemeral=True)

# Main Execution
if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_TOKEN"))
