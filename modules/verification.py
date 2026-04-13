import discord
from discord.ext import commands
from discord import ui
import asyncio
from typing import Optional

from data_manager import dm
from logger import logger


class VerifyView(ui.View):
    def __init__(self, verification_system):
        super().__init__(timeout=None)
        self.verification = verification_system
    
    @ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.verification.handle_verify(interaction)


class Verification:
    def __init__(self, bot):
        self.bot = bot
        self.unverified_role = None
        self.verified_role = None
        self._verify_channel_id = None
        self._load_settings()
    
    def _load_settings(self):
        data = dm.load_json("verification_settings", default={})
        self._verify_channel_id = data.get("verify_channel_id")
    
    def _save_settings(self):
        data = {
            "verify_channel_id": self._verify_channel_id
        }
        dm.save_json("verification_settings", data)
    
    async def setup(self, guild: discord.Guild):
        unverified = discord.utils.get(guild.roles, name="Unverified")
        verified = discord.utils.get(guild.roles, name="Verified")
        
        if not unverified:
            unverified = await guild.create_role(
                name="Unverified",
                color=discord.Color.grey(),
                hoist=False,
                mentionable=True
            )
            logger.info(f"Created Unverified role in {guild.name}")
        
        if not verified:
            verified = await guild.create_role(
                name="Verified",
                color=discord.Color.green(),
                hoist=True,
                mentionable=False
            )
            logger.info(f"Created Verified role in {guild.name}")
        
        self.unverified_role = unverified
        self.verified_role = verified
        
        await self.lock_server(guild)
        
        return unverified, verified
    
    async def lock_server(self, guild: discord.Guild):
        existing_verify = discord.utils.get(guild.text_channels, name="verify")
        if existing_verify:
            await existing_verify.delete()
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            self.verified_role: discord.PermissionOverwrite(view_channel=True),
        }
        
        verify_channel = await guild.create_text_channel(
            "verify",
            overwrites=overwrites,
            topic="Click the button below to verify yourself and gain access to the server"
        )
        
        self._verify_channel_id = verify_channel.id
        self._save_settings()
        
        embed = discord.Embed(
            title="Verification Required",
            description="Welcome to the server! To access all channels, please verify yourself by clicking the button below.",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="What is verification?",
            value="Verification grants you access to all server channels and features.",
            inline=False
        )
        
        view = VerifyView(self)
        await verify_channel.send(embed=embed, view=view)
        
        for category in guild.categories:
            await self._lock_category(category)
        
        for channel in guild.text_channels + guild.voice_channels:
            if channel.category is None:
                await self._lock_channel(channel)
        
        logger.info(f"Locked server {guild.name} - only Verified role can access channels")
    
    async def _lock_category(self, category: discord.CategoryChannel):
        try:
            new_category = await category.clone(name=f"{category.name} (Private)")
            
            for channel in category.channels:
                await self._lock_and_copy_channel(channel, new_category)
            
            await category.delete()
        except Exception as e:
            logger.error(f"Error locking category {category.name}: {e}")
    
    async def _lock_channel(self, channel: discord.TextChannel | discord.VoiceChannel):
        try:
            await self._lock_and_copy_channel(channel, None)
        except Exception as e:
            logger.error(f"Error locking channel {channel.name}: {e}")
    
    async def _lock_and_copy_channel(self, original_channel, new_category):
        if isinstance(original_channel, discord.TextChannel):
            overwrites = {
                self.verified_role: discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                    manage_messages=False,
                    manage_channels=False
                )
            }
            
            new_channel = await original_channel.clone(
                name=original_channel.name,
                category=new_category,
                overwrites=overwrites
            )
        elif isinstance(original_channel, discord.VoiceChannel):
            overwrites = {
                self.verified_role: discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True
                )
            }
            
            new_channel = await original_channel.clone(
                name=original_channel.name,
                category=new_category,
                overwrites=overwrites
            )
        
        try:
            await original_channel.delete()
        except:
            pass
        
        return new_channel
    
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        
        if not self.unverified_role or not self.verified_role:
            guild = member.guild
            self.unverified_role = discord.utils.get(guild.roles, name="Unverified")
            self.verified_role = discord.utils.get(guild.roles, name="Verified")
            
            if not self.unverified_role or not self.verified_role:
                logger.warning("Verification roles not set up in guild")
                return
        
        try:
            await member.add_roles(self.unverified_role)
            logger.info(f"Gave Unverified role to {member.display_name}")
        except Exception as e:
            logger.error(f"Error giving Unverified role: {e}")
    
    async def handle_verify(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild
        
        if not self.unverified_role:
            self.unverified_role = discord.utils.get(guild.roles, name="Unverified")
        if not self.verified_role:
            self.verified_role = discord.utils.get(guild.roles, name="Verified")
        
        if not self.unverified_role or not self.verified_role:
            await interaction.response.send_message("Verification not set up. Contact an admin.", ephemeral=True)
            return
        
        has_unverified = self.unverified_role in member.roles
        has_verified = self.verified_role in member.roles
        
        if has_verified:
            await interaction.response.send_message("You are already verified!", ephemeral=True)
            return
        
        try:
            if has_unverified:
                await member.remove_roles(self.unverified_role)
            
            await member.add_roles(self.verified_role)
            
            embed = discord.Embed(
                title="Verification Complete!",
                description=f"Welcome to {guild.name}! You now have access to all channels.",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            logger.info(f"Verified member: {member.display_name}")
            
        except Exception as e:
            logger.error(f"Error during verification: {e}")
            await interaction.response.send_message("An error occurred during verification. Contact an admin.", ephemeral=True)
    
    async def setup_interaction(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can run this command.", ephemeral=True)
            return
        
        guild = interaction.guild
        await interaction.response.send_message("Setting up verification system...", ephemeral=True)
        
        unverified, verified = await self.setup(guild)
        
        embed = discord.Embed(
            title="Verification System Set Up",
            description=f"Successfully created roles and locked server.\n\n"
                       f"**Unverified Role:** {unverified.mention}\n"
                       f"**Verified Role:** {verified.mention}",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    def get_verify_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        if self._verify_channel_id:
            return guild.get_channel(self._verify_channel_id)
        return None