import discord
from discord import ui
import datetime
import json
from data_manager import dm
from typing import Dict, Optional

class StaffApplicationModal(ui.Modal):
    def __init__(self, bot):
        super().__init__(title='Staff Application')
        self.bot = bot
        
    q1 = ui.TextInput(label='Why do you want to be staff?', style=discord.TextStyle.paragraph)
    q2 = ui.TextInput(label='What experience do you have?', style=discord.TextStyle.paragraph)
    q3 = ui.TextInput(label='Weekly Activity (hours)?', placeholder='e.g. 15-20 hours')
    q4 = ui.TextInput(label='What skills do you bring?', style=discord.TextStyle.paragraph)
    q5 = ui.TextInput(label='Anything else?', required=False, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        log_channel_id = dm.get_guild_data(guild_id, "staff_log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id)

        if not log_channel:
            return await interaction.response.send_message("Staff logs channel not found. Tell admin.", ephemeral=True)

        embed = discord.Embed(title=f"New Staff Application: {interaction.user.name}", color=discord.Color.gold())
        embed.add_field(name="User ID", value=interaction.user.id)
        embed.add_field(name="1. Why?", value=self.q1.value, inline=False)
        embed.add_field(name="2. Experience", value=self.q2.value, inline=False)
        embed.add_field(name="3. Activity", value=self.q3.value, inline=False)
        embed.add_field(name="4. Skills", value=self.q4.value, inline=False)
        embed.add_field(name="5. Extra", value=self.q5.value or "N/A", inline=False)

        view = ui.View()
        approve_btn = ui.Button(label="Approve", style=discord.ButtonStyle.success)
        deny_btn = ui.Button(label="Deny", style=discord.ButtonStyle.danger)

        async def approve_callback(it: discord.Interaction):
            await interaction.user.send("Your staff application has been approved!")
            await it.response.edit_message(content=f"✅ Approved by {it.user.name}", view=None)
             
            # Store in applications.json
            apps = dm.load_json("applications", default={})
            apps[str(interaction.user.id)] = {"status": "approved", "timestamp": str(datetime.datetime.now())}
            dm.save_json("applications", apps)
            
            # Also track in promotion system for fast-track consideration
            guild_id = interaction.guild.id
            promo_config = dm.get_guild_data(guild_id, "staff_promo_config", {})
            staff_applications = promo_config.get("staff_applications", {})
            staff_applications[str(interaction.user.id)] = {
                "status": "approved",
                "timestamp": str(datetime.datetime.now()),
                "approved_by": str(it.user.id),
                "application_data": {
                    "q1": self.q1.value,
                    "q2": self.q2.value,
                    "q3": self.q3.value,
                    "q4": self.q4.value,
                    "q5": self.q5.value
                }
            }
            promo_config["staff_applications"] = staff_applications
            dm.update_guild_data(guild_id, "staff_promo_config", promo_config)

        async def deny_callback(it: discord.Interaction):
            await interaction.user.send("Thank you for applying, but your application has been denied at this time.")
            await it.response.edit_message(content=f"❌ Denied by {it.user.name}", view=None)
             
            # Store in applications.json
            apps = dm.load_json("applications", default={})
            apps[str(interaction.user.id)] = {"status": "denied", "timestamp": str(datetime.datetime.now())}
            dm.save_json("applications", apps)
            
            # Also track in promotion system
            guild_id = interaction.guild.id
            promo_config = dm.get_guild_data(guild_id, "staff_promo_config", {})
            staff_applications = promo_config.get("staff_applications", {})
            staff_applications[str(interaction.user.id)] = {
                "status": "denied",
                "timestamp": str(datetime.datetime.now()),
                "denied_by": str(it.user.id),
                "application_data": {
                    "q1": self.q1.value,
                    "q2": self.q2.value,
                    "q3": self.q3.value,
                    "q4": self.q4.value,
                    "q5": self.q5.value
                }
            }
            promo_config["staff_applications"] = staff_applications
            dm.update_guild_data(guild_id, "staff_promo_config", promo_config)

        approve_btn.callback = approve_callback
        deny_btn.callback = deny_callback
        view.add_item(approve_btn)
        view.add_item(deny_btn)

        await log_channel.send(embed=embed, view=view)
        await interaction.response.send_message("Application submitted! We will DM you soon.", ephemeral=True)

class StaffSystem:
    def __init__(self, bot):
        self.bot = bot

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # 1. Create Channels
        public_channel = await guild.create_text_channel("apply-staff")
        log_channel = await guild.create_text_channel("apply-staff-logs", overwrites={
            guild.default_role: discord.PermissionOverwrite(read_messages=False)
        })

        dm.update_guild_data(guild.id, "staff_log_channel_id", log_channel.id)

        # 2. Public Panel
        embed = discord.Embed(title="Join Our Staff Team", description="Click below to apply for a staff position!", color=discord.Color.green())
        view = ui.View()
        apply_btn = ui.Button(label="Apply Now", style=discord.ButtonStyle.success)
        
        async def apply_callback(it: discord.Interaction):
            # Check cooldown here (Task requirement)
            apps = dm.load_json("applications", default={})
            last_app = apps.get(str(it.user.id))
            if last_app and (datetime.datetime.now() - datetime.datetime.fromisoformat(last_app['timestamp'])).days < 30:
                return await it.response.send_message("You can only apply once every 30 days.", ephemeral=True)
            
            await it.response.send_modal(StaffApplicationModal(self.bot))

        apply_btn.callback = apply_callback
        view.add_item(apply_btn)
        await public_channel.send(embed=embed, view=view)

        # 3. Mandatory Auto-Documentation
        help_embed = discord.Embed(title="Apply Staff System Help", description="Information about staff application system.", color=discord.Color.blue())
        help_embed.add_field(name="!apply status", value="Checks the status of your application.")
        help_embed.add_field(name="!help staffapply", value="Shows this help embed.")
        
        await public_channel.send(embed=help_embed)
        
        # 4. Register Prefix Commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        # !apply command - checks application status
        custom_cmds["apply"] = json.dumps({
            "command_type": "application_status"
        })
        
        # !help staffapply command - shows help embed
        custom_cmds["help staffapply"] = json.dumps({
            "command_type": "help_embed",
            "title": "Apply Staff System Help",
            "description": "Information about staff application system.",
            "fields": [
                {
                    "name": "!apply status",
                    "value": "Checks the status of your application.",
                    "inline": False
                },
                {
                    "name": "!help staffapply",
                    "value": "Shows this help embed.",
                    "inline": False
                }
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        return True
