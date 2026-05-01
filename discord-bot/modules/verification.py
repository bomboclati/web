import discord
from discord.ext import commands
from discord import ui
import asyncio
import time
import random
from datetime import datetime, timezone
from typing import Optional

from data_manager import dm
from logger import logger

class CaptchaModal(ui.Modal, title="🧮 Verification CAPTCHA"):
    answer = ui.TextInput(label="Solve the math problem", placeholder="Type the answer", required=True, max_length=8)

    def __init__(self, verification, expected: int, question: str):
        super().__init__()
        self.verification = verification
        self.expected = expected
        self.answer.label = question

    async def on_submit(self, interaction: discord.Interaction):
        try:
            given = int(self.answer.value.strip())
        except ValueError:
            return await interaction.response.send_message("❌ That's not a number. Click Verify again to retry.", ephemeral=True)
        if given != self.expected:
            return await interaction.response.send_message("❌ Wrong answer. Click Verify again to retry.", ephemeral=True)
        await self.verification._grant_verified(interaction, method="captcha")

class VerifyView(ui.View):
    def __init__(self, verification_system=None):
        super().__init__(timeout=None)
        self.verification = verification_system

    @ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="verify_button_v2")
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.verification:
            from modules.verification import Verification
            self.verification = Verification(interaction.client)
        await self.verification.handle_verify(interaction)

class Verification:
    def __init__(self, bot):
        self.bot = bot

    def _get_admin_config(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "verification_config", {
            "enabled": True,
            "min_account_age_days": 0,
            "captcha_enabled": False,
            "welcome_dm": "Welcome {user} to {server}!",
            "verification_log": []
        })

    def _get_roles(self, guild: discord.Guild):
        config = self._get_admin_config(guild.id)
        uv_id = config.get("unverified_role_id")
        v_id = config.get("verified_role_id") or config.get("role_id")

        unverified = guild.get_role(uv_id) if uv_id else discord.utils.get(guild.roles, name="Unverified")
        verified = guild.get_role(v_id) if v_id else discord.utils.get(guild.roles, name="Verified")
        return unverified, verified

    async def setup(self, guild: discord.Guild):
        unverified = discord.utils.get(guild.roles, name="Unverified") or await guild.create_role(name="Unverified", color=discord.Color.greyple())
        verified = discord.utils.get(guild.roles, name="Verified") or await guild.create_role(name="Verified", color=discord.Color.green())

        # Lock server logic
        for category in guild.categories:
            await category.set_permissions(guild.default_role, view_channel=False)
            await category.set_permissions(verified, view_channel=True)
            await category.set_permissions(unverified, view_channel=False)

        # Ensure #rules and #verify are accessible
        rules = discord.utils.get(guild.text_channels, name="rules")
        verify_ch = discord.utils.get(guild.text_channels, name="verify") or await guild.create_text_channel("verify")

        for ch in [rules, verify_ch]:
            if ch:
                await ch.set_permissions(guild.default_role, view_channel=False)
                await ch.set_permissions(unverified, view_channel=True, send_messages=False)
                await ch.set_permissions(verified, view_channel=True)

        # Update config
        config = self._get_admin_config(guild.id)
        config["unverified_role_id"] = unverified.id
        config["verified_role_id"] = verified.id
        config["channel_id"] = verify_ch.id
        dm.update_guild_data(guild.id, "verification_config", config)

        # Post button
        embed = discord.Embed(title="🛡️ Verification Required", description=f"Welcome to **{guild.name}**. Click below to verify.", color=discord.Color.blue())
        await verify_ch.send(embed=embed, view=VerifyView(self))
        return unverified, verified

    async def handle_verify(self, interaction: discord.Interaction):
        member = interaction.user
        config = self._get_admin_config(interaction.guild.id)
        if not config.get("enabled", True): return await interaction.response.send_message("Disabled.", ephemeral=True)

        unverified, verified = self._get_roles(interaction.guild)
        if verified in member.roles: return await interaction.response.send_message("Already verified.", ephemeral=True)

        # Account age check
        min_age = config.get("min_account_age_days", 0)
        if min_age > 0:
            if (discord.utils.utcnow() - member.created_at).days < min_age:
                return await interaction.response.send_message(f"Account too new ({min_age}d req).", ephemeral=True)

        # Account age check passed, proceed with verification

        # CAPTCHA
        if config.get("captcha_enabled"):
            a, b = random.randint(1, 10), random.randint(1, 10)
            return await interaction.response.send_modal(CaptchaModal(self, a+b, f"What is {a} + {b}?"))

        await self._grant_verified(interaction, "button")

    async def _grant_verified(self, interaction: discord.Interaction, method: str):
        member = interaction.user
        guild = interaction.guild
        uv, v = self._get_roles(guild)
        config = self._get_admin_config(guild.id)

        try:
            if uv: await member.remove_roles(uv)
            if v: await member.add_roles(v)

            # Log
            log = config.get("verification_log", [])
            log.append({"user_id": member.id, "ts": time.time(), "method": method})
            config["verification_log"] = log[-100:]
            dm.update_guild_data(guild.id, "verification_config", config)

            # DM
            welcome = config.get("welcome_dm", "").replace("{user}", member.mention).replace("{server}", guild.name)
            try: await member.send(welcome)
            except: pass

            await interaction.response.send_message("✅ Verified!", ephemeral=True)
        except Exception as e:
            logger.error(f"Verify error: {e}")

    async def on_member_join(self, member):
        uv, v = self._get_roles(member.guild)
        if uv: await member.add_roles(uv)

    async def setup_interaction(self, interaction):
        await self.setup(interaction.guild)
        await interaction.followup.send("Verification System Setup Complete.")

    async def set_verify_channel(self, message, args: list):
        """Handle !setverifychannel command to set the verification channel"""
        import asyncio

        # Check admin permissions with enhanced feedback
        if not message.author.guild_permissions.administrator:
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need **Administrator** permissions to configure verification settings.",
                color=discord.Color.red()
            )
            embed.set_footer(text="Contact a server administrator for assistance")
            return await message.channel.send(embed=embed)

        # Loading animation
        loading_embed = discord.Embed(
            title="⚙️ Configuring Verification Channel",
            description="🔄 Analyzing channel settings...\n🔄 Updating configuration...\n🔄 Posting verification interface...",
            color=discord.Color.orange()
        )
        loading_msg = await message.channel.send(embed=loading_embed)

        # Get target channel with better parsing
        target_channel = None
        if message.channel_mentions:
            target_channel = message.channel_mentions[0]
        elif args and len(args) > 1:
            channel_arg = args[1].strip("<#>")
            # Try to parse as channel ID
            try:
                channel_id = int(channel_arg)
                target_channel = message.guild.get_channel(channel_id)
            except (ValueError, IndexError):
                # Try to find by name
                target_channel = discord.utils.get(message.guild.text_channels, name=channel_arg)

        if not target_channel:
            target_channel = message.channel

        if not isinstance(target_channel, discord.TextChannel):
            error_embed = discord.Embed(
                title="❌ Invalid Channel",
                description="Please specify a valid **text channel** for verification.",
                color=discord.Color.red()
            )
            error_embed.add_field(
                name="Usage Examples",
                value="`!setverifychannel #verification`\n`!setverifychannel 123456789012345678`\n`!setverifychannel` (uses current channel)",
                inline=False
            )
            await loading_msg.edit(embed=error_embed)
            return

        # Update config with animation steps
        await asyncio.sleep(0.5)
        loading_embed.description = "✅ Analyzing channel settings...\n🔄 Updating configuration...\n🔄 Posting verification interface..."
        await loading_msg.edit(embed=loading_embed)

        config = self._get_admin_config(message.guild.id)
        config["channel_id"] = target_channel.id
        dm.update_guild_data(message.guild.id, "verification_config", config)

        await asyncio.sleep(0.5)
        loading_embed.description = "✅ Analyzing channel settings...\n✅ Updating configuration...\n🔄 Posting verification interface..."
        await loading_msg.edit(embed=loading_embed)

        # Post enhanced verify embed and button
        verify_embed = discord.Embed(
            title="🛡️ Server Verification Required",
            description=f"Welcome to **{message.guild.name}**! To access the server, you must complete verification.\n\n"
                       "Click the **✅ Verify** button below to start the process.",
            color=discord.Color.blue()
        )

        verify_embed.add_field(
            name="🔒 Security Features",
            value="• Account age verification\n• CAPTCHA challenge\n• Automated role assignment",
            inline=False
        )

        verify_embed.set_footer(text="Verification is required for all new members • Protected by Guardian AI")
        verify_embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/123456789012345678/123456789012345678/verification_shield.png")

        verify_msg = await target_channel.send(embed=verify_embed, view=VerifyView(self))

        # Add reaction animation
        await verify_msg.add_reaction("✅")
        await verify_msg.add_reaction("🛡️")

        await asyncio.sleep(0.5)
        loading_embed.description = "✅ Analyzing channel settings...\n✅ Updating configuration...\n✅ Posting verification interface..."
        await loading_msg.edit(embed=loading_embed)

        # Success message with animation
        success_embed = discord.Embed(
            title="✅ Verification Channel Configured",
            description=f"Successfully set {target_channel.mention} as the verification channel!",
            color=discord.Color.green()
        )

        success_embed.add_field(
            name="📋 What's Next",
            value="• Verification button is now active in the channel\n• New members will be prompted to verify\n• Use `!configpanel verification` to adjust settings",
            inline=False
        )

        success_embed.set_footer(text=f"Configured by {message.author.display_name}")
        await loading_msg.edit(embed=success_embed)

        # Celebration reactions
        await loading_msg.add_reaction("🎉")
        await loading_msg.add_reaction("✅")
