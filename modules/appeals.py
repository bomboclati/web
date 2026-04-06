import discord
from discord import ui
from data_manager import dm
import datetime
import json

class AppealModal(ui.Modal, title='Moderation Appeal'):
    reason = ui.TextInput(label='Why should your action be reversed?', style=discord.TextStyle.paragraph)

    def __init__(self, bot, guild_id, action_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.action_id = action_id

    async def on_submit(self, interaction: discord.Interaction):
        guild = self.bot.get_guild(self.guild_id)
        appeal_channel_id = dm.get_guild_data(self.guild_id, "appeal_channel_id")
        appeal_channel = guild.get_channel(appeal_channel_id)

        if not appeal_channel:
            return await interaction.response.send_message("Appeal channel not found.", ephemeral=True)

        embed = discord.Embed(title=f"New Appeal: {interaction.user.name}", color=discord.Color.orange())
        embed.add_field(name="User ID", value=interaction.user.id)
        embed.add_field(name="Action ID", value=self.action_id)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)

        view = ui.View()
        approve_btn = ui.Button(label="Accept Appeal", style=discord.ButtonStyle.success)
        deny_btn = ui.Button(label="Deny Appeal", style=discord.ButtonStyle.danger)

        async def approve_callback(it: discord.Interaction):
            # Logic to unban/untimeout
            await interaction.user.send(f"Your appeal for action {self.action_id} in {guild.name} has been accepted.")
            await it.response.edit_message(content=f"✅ Appeal Accepted by {it.user.name}", view=None)

        async def deny_callback(it: discord.Interaction):
            await interaction.user.send(f"Your appeal for action {self.action_id} in {guild.name} has been denied.")
            await it.response.edit_message(content=f"❌ Appeal Denied by {it.user.name}", view=None)

        approve_btn.callback = approve_callback
        deny_btn.callback = deny_callback
        view.add_item(approve_btn)
        view.add_item(deny_btn)

        await appeal_channel.send(embed=embed, view=view)
        await interaction.response.send_message("Appeal submitted!", ephemeral=True)

class Appeals:
    def __init__(self, bot):
        self.bot = bot

    async def setup(self, interaction: discord.Interaction):
        guild = interaction.guild
        appeal_channel = await guild.create_text_channel("appeals-logs", overwrites={
            guild.default_role: discord.PermissionOverwrite(read_messages=False)
        })
        dm.update_guild_data(guild.id, "appeal_channel_id", appeal_channel.id)

        # Auto-documentation
        help_embed = discord.Embed(title="Appeals System", description="Handles moderation appeals via DMs.", color=discord.Color.blue())
        help_embed.add_field(name="!appeal status", value="Checks the status of your appeal.")
        help_embed.add_field(name="!help appeals", value="Shows this help embed.")
        await appeal_channel.send(embed=help_embed)

        # Register Prefix Commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        # !appeal command - checks appeal status
        custom_cmds["appeal"] = json.dumps({
            "command_type": "appeal_status"
        })
        
        # !help appeals command - shows help embed
        custom_cmds["help appeals"] = json.dumps({
            "command_type": "help_embed",
            "title": "Appeals System",
            "description": "Handles moderation appeals via DMs.",
            "fields": [
                {
                    "name": "!appeal status",
                    "value": "Checks the status of your appeal.",
                    "inline": False
                },
                {
                    "name": "!help appeals",
                    "value": "Shows this help embed.",
                    "inline": False
                }
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        return True

    async def send_appeal_dm(self, user: discord.User, guild_id: int, action_id: str):
        """Sends a DM to a penalized user with an appeal button."""
        embed = discord.Embed(title="You have been penalized", description=f"Guild ID: {guild_id}\nAction ID: {action_id}", color=discord.Color.red())
        view = ui.View()
        appeal_btn = ui.Button(label="Appeal Now", style=discord.ButtonStyle.secondary)
        
        async def appeal_callback(it: discord.Interaction):
            await it.response.send_modal(AppealModal(self.bot, guild_id, action_id))

        appeal_btn.callback = appeal_callback
        view.add_item(appeal_btn)
        await user.send(embed=embed, view=view)
