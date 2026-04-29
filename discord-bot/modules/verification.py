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
