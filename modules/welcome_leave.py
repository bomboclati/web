import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional

from data_manager import dm
from logger import logger


class WelcomeLeaveSystem:
    def __init__(self, bot):
        self.bot = bot
        self._member_counters: Dict[int, int] = {}
        self._load_settings()

    def _load_settings(self):
        data = dm.load_json("welcome_leave_settings", default={})
        self._member_counters = data.get("member_counters", {})

    def _save_settings(self):
        data = {"member_counters": self._member_counters}
        dm.save_json("welcome_leave_settings", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "welcome_leave_settings", {
            "enabled": True,
            "welcome_channel": None,
            "welcome_message": "Welcome {user} to {server}! 🎉",
            "welcome_dm": None,
            "leave_channel": None,
            "leave_message": "{user} has left {server}. Goodbye! 👋",
            "join_roles": [],
            "verify_on_join": False,
            "verify_role": None,
            "member_count_channel": None,
            "member_count_format": "Members: {count}"
        })

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        
        guild_id = member.guild.id
        settings = self.get_guild_settings(guild_id)
        
        for role_id in settings.get("join_roles", []):
            role = member.guild.get_role(int(role_id))
            if role:
                try:
                    await member.add_roles(role)
                except:
                    pass
        
        if settings.get("verify_on_join"):
            verify_role_id = settings.get("verify_role")
            if verify_role_id:
                role = member.guild.get_role(int(verify_role_id))
                if role:
                    try:
                        await member.add_roles(role)
                    except:
                        pass
        
        welcome_channel_id = settings.get("welcome_channel")
        if welcome_channel_id:
            welcome_channel = member.guild.get_channel(int(welcome_channel_id))
            if welcome_channel:
                message = settings.get("welcome_message", "Welcome {user} to {server}!")
                
                message = message.replace("{user}", member.mention)
                message = message.replace("{username}", member.display_name)
                message = message.replace("{server}", member.guild.name)
                message = message.replace("{count}", str(member.guild.member_count))
                
                embed = discord.Embed(
                    description=message,
                    color=discord.Color.green()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                
                await welcome_channel.send(embed=embed)
        
        welcome_dm = settings.get("welcome_dm")
        if welcome_dm:
            try:
                message = welcome_dm.replace("{user}", member.display_name)
                message = message.replace("{server}", member.guild.name)
                
                await member.send(message)
            except:
                pass
        
        await self._update_member_count(member.guild)

    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        
        guild_id = member.guild.id
        settings = self.get_guild_settings(guild_id)
        
        leave_channel_id = settings.get("leave_channel")
        if leave_channel_id:
            leave_channel = member.guild.get_channel(int(leave_channel_id))
            if leave_channel:
                message = settings.get("leave_message", "{user} has left {server}.")
                
                message = message.replace("{user}", member.display_name)
                message = message.replace("{server}", member.guild.name)
                message = message.replace("{count}", str(member.guild.member_count))
                
                embed = discord.Embed(
                    description=message,
                    color=discord.Color.red()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                
                await leave_channel.send(embed=embed)
        
        await self._update_member_count(member.guild)

    async def _update_member_count(self, guild: discord.Guild):
        settings = self.get_guild_settings(guild.id)
        counter_channel_id = settings.get("member_count_channel")
        
        if not counter_channel_id:
            return
        
        counter_channel = guild.get_channel(int(counter_channel_id))
        if not counter_channel:
            return
        
        count_format = settings.get("member_count_format", "Members: {count}")
        count_format = count_format.replace("{count}", str(guild.member_count))
        
        try:
            await counter_channel.edit(name=count_format[:100])
        except:
            pass

    async def generate_welcome_message(self, guild: discord.Guild, member: discord.Member) -> str:
        prompt = f"""Generate a welcome message for a new member.

SERVER: {guild.name}
NEW MEMBER: {member.display_name}
MEMBER COUNT: {guild.member_count}

Respond with JSON only:
{{
    "message": "A warm, creative welcome message (1-2 sentences)",
    "tip": "One helpful tip for new members"
}}

Make it fun and welcoming!"""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild.id,
                user_id=member.id,
                user_input=prompt,
                system_prompt="You write welcoming messages. Be warm, creative, and concise."
            )
            
            return result.get("message", f"Welcome {member.mention} to {guild.name}!")
        except:
            return f"Welcome {member.mention} to {guild.name}!"

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "welcome_leave_settings", settings)
        
        try:
            doc_channel = await guild.create_text_channel("welcome-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        doc_embed = discord.Embed(title="👋 Welcome/Leave System Guide", description="Complete guide!", color=discord.Color.green())
        doc_embed.add_field(name="📖 How It Works", value="Automatically welcomes new members and says goodbye when they leave. Can assign roles and send DMs.", inline=False)
        doc_embed.add_field(name="Available Variables", value="• {user} - Mention user\n• {username} - Username\n• {server} - Server name\n• {member_count} - Total members", inline=False)
        doc_embed.add_field(name="Features", value="• Custom welcome/leave messages\n• Role assignment on join\n• Member counter channel\n• Welcome DMs", inline=False)
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** Members will see welcome messages automatically when they join!")
        
        help_embed = discord.Embed(title="👋 Welcome/Leave System", description="Custom welcome and leave messages with role assignment.", color=discord.Color.green())
        help_embed.add_field(name="How it works", value="Automatically sends welcome messages when members join/leave. Can assign roles, update member count channels, and send DMs.", inline=False)
        help_embed.add_field(name="!welcome", value="Test welcome message.", inline=False)
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["welcome"] = json.dumps({
            "command_type": "test_welcome"
        })
        custom_cmds["help welcomeleave"] = json.dumps({
            "command_type": "help_embed",
            "title": "👋 Welcome/Leave System",
            "description": "Welcome and leave messages.",
            "fields": [
                {"name": "!welcome", "value": "Test welcome message.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
