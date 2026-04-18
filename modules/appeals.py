import discord
from discord import ui
from data_manager import dm
import datetime
import json
import time

class AppealModal(ui.Modal, title='Moderation Appeal'):
    reason = ui.TextInput(
        label='Why should your action be reversed?',
        style=discord.TextStyle.paragraph,
        min_length=20,
        max_length=1000,
        placeholder="Provide details on why we should reconsider..."
    )
    
    def __init__(self, bot, guild_id, action_id, category: str = "ban"):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.action_id = action_id
        self.category = category
        
        # Dynamic title based on category
        titles = {
            "ban": "Ban Appeal",
            "mute": "Mute Appeal", 
            "warn": "Warning Appeal"
        }
        self.title = titles.get(category, "Moderation Appeal")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.followup.send("❌ Error: Guild not found.", ephemeral=True)

        appeal_channel_id = dm.get_guild_data(self.guild_id, "appeal_channel_id")
        appeal_channel = guild.get_channel(appeal_channel_id)
        
        if not appeal_channel:
            return await interaction.followup.send("❌ Error: Appeal channel not found. Please contact staff.", ephemeral=True)
        
        embed = discord.Embed(title=f"New Appeal: {interaction.user.name}", color=discord.Color.orange())
        embed.add_field(name="User ID", value=interaction.user.id, inline=True)
        embed.add_field(name="Action ID", value=self.action_id, inline=True)
        embed.add_field(name="Category", value=self.category.title(), inline=True)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        
        view = ui.View()
        approve_btn = ui.Button(label="Accept", style=discord.ButtonStyle.success)
        deny_btn = ui.Button(label="Deny", style=discord.ButtonStyle.danger)
        evidence_btn = ui.Button(label="Request Evidence", style=discord.ButtonStyle.secondary)
        
        async def approve_callback(it: discord.Interaction):
            from modules.appeals import Appeals
            appeals = Appeals(self.bot)
            await appeals.resolve_appeal(self.action_id, True, it.user)
            await interaction.user.send(f"Your {self.category} appeal in {guild.name} has been accepted!")
            await it.response.edit_message(content=f"✅ Appeal Accepted by {it.user.name}", view=None)
        
        async def deny_callback(it: discord.Interaction):
            await interaction.user.send(f"Your {self.category} appeal in {guild.name} has been denied.")
            await it.response.edit_message(content=f"❌ Appeal Denied by {it.user.name}", view=None)
        
        async def evidence_callback(it: discord.Interaction):
            await it.response.send_message("What evidence would you like the user to provide?", ephemeral=True)
        
        approve_btn.callback = approve_callback
        deny_btn.callback = deny_callback
        evidence_btn.callback = evidence_callback
        
        view.add_item(approve_btn)
        view.add_item(deny_btn)
        view.add_item(evidence_btn)
        
        await appeal_channel.send(embed=embed, view=view)
        
        # Save appeal to history
        self._save_appeal_history(interaction.user.id, self.guild_id, self.action_id, self.category)
        
        await interaction.response.send_message("Appeal submitted!", ephemeral=True)
    
    def _save_appeal_history(self, user_id: int, guild_id: int, action_id: str, category: str):
        history = dm.get_guild_data(guild_id, "appeal_history", {})
        
        if str(user_id) not in history:
            history[str(user_id)] = []
        
        history[str(user_id)].append({
            "action_id": action_id,
            "category": category,
            "submitted_at": time.time(),
            "status": "pending"
        })
        
        dm.update_guild_data(guild_id, "appeal_history", history)


class Appeals:
    """
    Handles moderation appeals via DMs.
    Now includes categories, evidence, history, auto-expire!
    """
    
    APPEAL_CATEGORIES = {
        "ban": {"label": "Ban", "emoji": "🔨", "color": discord.Color.red()},
        "mute": {"label": "Mute", "emoji": "🔇", "color": discord.Color.orange()},
        "warn": {"label": "Warning", "emoji": "⚠️", "color": discord.Color.yellow()}
    }
    
    def __init__(self, bot):
        self.bot = bot
    
    async def request_appeal(self, user: discord.User, guild_id: int, action_id: str, category: str = "ban"):
        """Send appeal request to user."""
        category_info = self.APPEAL_CATEGORIES.get(category, self.APPEAL_CATEGORIES["ban"])
        
        embed = discord.Embed(
            title=f"Your {category_info['label']} Has Been Appealed",
            description=f"Guild ID: {guild_id}\nAction ID: {action_id}",
            color=category_info['color']
        )
        
        view = ui.View()
        appeal_btn = ui.Button(label=f"Appeal {category_info['label']}", style=discord.ButtonStyle.secondary)
        
        async def appeal_callback(it: discord.Interaction):
            await it.response.send_modal(AppealModal(self.bot, guild_id, action_id, category))
        
        appeal_btn.callback = appeal_callback
        view.add_item(appeal_btn)
        
        await user.send(embed=embed, view=view)
    
    async def resolve_appeal(self, action_id: str, accepted: bool, resolved_by: discord.Member):
        """Mark appeal as resolved."""
        appeals = dm.load_json("pending_appeals", default={})
        
        if action_id in appeals:
            appeals[action_id]["status"] = "accepted" if accepted else "denied"
            appeals[action_id]["resolved_by"] = resolved_by.id
            appeals[action_id]["resolved_at"] = time.time()
            dm.save_json("pending_appeals", appeals)
    
    def get_appeal_history(self, guild_id: int, user_id: int) -> list:
        """Get user's appeal history."""
        history = dm.get_guild_data(guild_id, "appeal_history", {})
        return history.get(str(user_id), [])
    
    async def cleanup_old_appeals(self, guild_id: int, days: int = 30):
        """Auto-expire old pending appeals."""
        history = dm.get_guild_data(guild_id, "appeal_history", {})
        cutoff = time.time() - (days * 86400)
        
        cleaned = {}
        cleaned_count = 0
        
        for user_id, appeals in history.items():
            kept = []
            for appeal in appeals:
                if appeal.get("status") == "pending":
                    submitted = appeal.get("submitted_at", 0)
                    if submitted < cutoff:
                        appeal["status"] = "expired"
                        cleaned_count += 1
                kept.append(appeal)
            
            if kept:
                cleaned[user_id] = kept
        
        if cleaned_count > 0:
            dm.update_guild_data(guild_id, "appeal_history", cleaned)
            logger.info(f"Cleaned {cleaned_count} expired appeals for guild {guild_id}")
        
        return cleaned_count
    
    async def setup(self, interaction: discord.Interaction, category: str = None):
        guild = interaction.guild
        appeal_channel = await guild.create_text_channel("appeals-logs", overwrites={
            guild.default_role: discord.PermissionOverwrite(read_messages=False)
        })
        dm.update_guild_data(guild.id, "appeal_channel_id", appeal_channel.id)
        
        help_embed = discord.Embed(
            title="Appeals System", 
            description="Handles moderation appeals via DMs.",
            color=discord.Color.blue()
        )
        
        fields = [
            ("!appeal status", "Check your appeal status."),
            ("!help appeals", "Show this help embed.")
        ]
        
        if category:
            fields.append((f"!appeal {category}", f"Appeal a specific {category}."))
        
        for name, value in fields:
            help_embed.add_field(name=name, value=value, inline=False)
        
        await appeal_channel.send(embed=help_embed)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["appeal"] = json.dumps({
            "command_type": "appeal_status"
        })
        
        custom_cmds["appeal ban"] = json.dumps({
            "command_type": "appeal_status"
        })
        
        custom_cmds["appeal mute"] = json.dumps({
            "command_type": "appeal_status"  
        })
        
        custom_cmds["appeal warn"] = json.dumps({
            "command_type": "appeal_status"
        })
        
        custom_cmds["help appeals"] = json.dumps({
            "command_type": "help_embed",
            "title": "Appeals System",
            "description": "Handles moderation appeals via DMs.",
            "fields": [
                {"name": "!appeal status", "value": "Check your appeal status.", "inline": False},
                {"name": "!appeal ban", "value": "Appeal a ban.", "inline": False},
                {"name": "!appeal mute", "value": "Appeal a mute.", "inline": False},
                {"name": "!appeal warn", "value": "Appeal a warning.", "inline": False},
                {"name": "!help appeals", "value": "Show this help.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        return True
    
    async def send_appeal_dm(self, user: discord.User, guild_id: int, action_id: str, category: str = "ban"):
        """Sends a DM to a penalized user with an appeal button."""
        category_info = self.APPEAL_CATEGORIES.get(category, self.APPEAL_CATEGORIES["ban"])
        
        embed = discord.Embed(
            title=f"You received a {category_info['label']}",
            description=f"Guild ID: {guild_id}\nAction ID: {action_id}",
            color=category_info['color']
        )
        
        view = ui.View()
        appeal_btn = ui.Button(label=f"Appeal {category_info['label']}", style=discord.ButtonStyle.secondary)
        
        async def appeal_callback(it: discord.Interaction):
            await it.response.send_modal(AppealModal(self.bot, guild_id, action_id, category))
        
        appeal_btn.callback = appeal_callback
        view.add_item(appeal_btn)
        
        await user.send(embed=embed, view=view)


from logger import logger