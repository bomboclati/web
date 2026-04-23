import discord
from discord.ext import commands
from discord import ui
import asyncio
from typing import Optional

from data_manager import dm
from logger import logger


class VerifyView(ui.View):
    def __init__(self, verification_system=None):
        super().__init__(timeout=None)
        self.verification = verification_system

    @ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.verification:
            # Fallback for when view is recovered from persistence in bot.py
            from modules.verification import Verification
            self.verification = Verification(interaction.client)
        await self.verification.handle_verify(interaction)


class Verification:
    def __init__(self, bot):
        self.bot = bot
        self._guild_cache: dict = {}

    # ──────────────────────────────────────────────
    # Persistence helpers (per-guild)
    # ──────────────────────────────────────────────

    def _load_guild(self, guild_id: int) -> dict:
        if guild_id not in self._guild_cache:
            data = dm.load_json(f"verification_settings_{guild_id}", default={})
            self._guild_cache[guild_id] = data
        return self._guild_cache[guild_id]

    def _save_guild(self, guild_id: int):
        dm.save_json(f"verification_settings_{guild_id}", self._guild_cache[guild_id])

    def _get_verify_channel_id(self, guild_id: int) -> Optional[int]:
        return self._load_guild(guild_id).get("verify_channel_id")

    def _set_verify_channel_id(self, guild_id: int, channel_id: int):
        self._load_guild(guild_id)["verify_channel_id"] = channel_id
        self._save_guild(guild_id)

    # ──────────────────────────────────────────────
    # Role helpers (resolved fresh each call)
    # ──────────────────────────────────────────────

    def _get_roles(self, guild: discord.Guild):
        unverified = discord.utils.get(guild.roles, name="Unverified")
        verified = discord.utils.get(guild.roles, name="Verified")
        return unverified, verified

    async def _ensure_roles(self, guild: discord.Guild):
        unverified, verified = self._get_roles(guild)

        if not unverified:
            unverified = await guild.create_role(
                name="Unverified",
                color=discord.Color.greyple(),
                hoist=False,
                mentionable=False,
                reason="Verification system setup",
            )
            logger.info(f"[Verification] Created Unverified role in {guild.name}")

        if not verified:
            verified = await guild.create_role(
                name="Verified",
                color=discord.Color.green(),
                hoist=True,
                mentionable=False,
                reason="Verification system setup",
            )
            logger.info(f"[Verification] Created Verified role in {guild.name}")

        return unverified, verified

    # ──────────────────────────────────────────────
    # Server lock
    # ──────────────────────────────────────────────

    async def setup(self, guild: discord.Guild):
        """
        Full setup:
        1. Create Unverified + Verified roles if missing.
        2. Make ALL existing channels/categories private (visible only to Verified).
        3. Create a #verify channel that only Unverified (and Verified) can see.
        """
        unverified, verified = await self._ensure_roles(guild)
        await self.lock_server(guild, unverified, verified)
        return unverified, verified

    async def lock_server(self, guild: discord.Guild, unverified: discord.Role, verified: discord.Role):
        """
        Lock every channel/category so that:
          - @everyone  -> cannot view (deny)
          - Unverified -> cannot view (deny)
          - Verified   -> can view (allow)

        Then create / update the #verify channel so that:
          - @everyone  -> cannot view (deny)
          - Unverified -> CAN view (allow, read-only)
          - Verified   -> can view (allow)
        """
        everyone = guild.default_role

        # 1. Lock all categories (edit permissions in-place, no renaming or cloning)
        for category in guild.categories:
            await self._lock_category(category, everyone, unverified, verified)
            await asyncio.sleep(0.4)  # rate-limit safety

        # 2. Lock uncategorised channels
        for channel in list(guild.text_channels) + list(guild.voice_channels):
            if channel.category is None and channel.name != "verify":
                await self._apply_lock(channel, everyone, unverified, verified)
                await asyncio.sleep(0.3)

        # 3. Create (or update) #verify channel — visible only to Unverified + Verified
        verify_overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            unverified: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                add_reactions=False,
            ),
            verified: discord.PermissionOverwrite(view_channel=True),
        }

        existing_verify = discord.utils.get(guild.text_channels, name="verify")
        if existing_verify:
            await existing_verify.edit(
                overwrites=verify_overwrites,
                topic="Click the button below to verify yourself and gain access to the server.",
                reason="Verification system setup",
            )
            verify_channel = existing_verify
            logger.info(f"[Verification] Updated existing #verify channel in {guild.name}")
        else:
            verify_channel = await guild.create_text_channel(
                "verify",
                overwrites=verify_overwrites,
                topic="Click the button below to verify yourself and gain access to the server.",
                reason="Verification system setup",
            )
            logger.info(f"[Verification] Created #verify channel in {guild.name}")

        self._set_verify_channel_id(guild.id, verify_channel.id)

        # Post the verification embed + button (remove old bot messages first)
        await verify_channel.purge(limit=10, check=lambda m: m.author.bot)

        embed = discord.Embed(
            title="🔒 Verification Required",
            description=(
                "Welcome to **{}**!\n\n"
                "To gain access to all channels and categories, click the **Verify** button below."
            ).format(guild.name),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="You will receive the Verified role immediately.")

        view = VerifyView(self)
        await verify_channel.send(embed=embed, view=view)

        logger.info(f"[Verification] Server lock complete for {guild.name}")

    async def _merge_channel_permission(self, channel, role, **kwargs):
        """Merge permission changes into existing overwrites instead of replacing them."""
        existing = channel.overwrites_for(role)
        for perm_name, perm_value in kwargs.items():
            setattr(existing, perm_name, perm_value)
        await channel.set_permissions(role, overwrite=existing)

    async def _lock_category(
        self,
        category: discord.CategoryChannel,
        everyone: discord.Role,
        unverified: discord.Role,
        verified: discord.Role,
    ):
        """Edit the category's permission overwrites in-place (no cloning or renaming)."""
        try:
            # Ensure bot always has access to prevent lockout
            await self._merge_channel_permission(category, self.bot.user, view_channel=True, manage_channels=True, manage_permissions=True)
            
            # Apply permissions properly using merge
            await self._merge_channel_permission(category, everyone, view_channel=False)
            if unverified:
                await self._merge_channel_permission(category, unverified, view_channel=False)
            if verified:
                await self._merge_channel_permission(category, verified, view_channel=True)

            # Lock every channel inside the category too
            for channel in category.channels:
                if channel.name != "verify":
                    await self._apply_lock(channel, everyone, unverified, verified)
                    await asyncio.sleep(0.2)

            logger.info(f"[Verification] Locked category: {category.name}")
        except Exception as e:
            logger.error(f"[Verification] Error locking category {category.name}: {e}")

    async def _apply_lock(
        self,
        channel,
        everyone: discord.Role,
        unverified: discord.Role,
        verified: discord.Role,
    ):
        """Edit a text or voice channel's overwrites in-place."""
        try:
            # Ensure bot always has access to prevent lockout
            await self._merge_channel_permission(channel, self.bot.user, view_channel=True, manage_channels=True, manage_permissions=True)
            
            # Apply permissions properly using merge
            await self._merge_channel_permission(channel, everyone, view_channel=False)
            if unverified:
                await self._merge_channel_permission(channel, unverified, view_channel=False)
            if verified:
                await self._merge_channel_permission(channel, verified, view_channel=True)

            logger.info(f"[Verification] Locked channel: {channel.name}")
        except Exception as e:
            logger.error(f"[Verification] Error locking channel {channel.name}: {e}")

    # ──────────────────────────────────────────────
    # Auto-lock newly created channels / categories
    # ──────────────────────────────────────────────

    async def on_guild_channel_create(self, channel):
        """Automatically lock any new channel or category so Unverified members
        can't see it. Skips the verify channel itself and only runs if the
        verification system is set up (Unverified role exists)."""
        guild = channel.guild
        if not guild:
            return

        unverified, verified = self._get_roles(guild)
        if not unverified:
            # Verification system not set up in this guild — do nothing
            return

        # Don't lock the verify channel
        verify_channel_id = self._get_verify_channel_id(guild.id)
        if channel.id == verify_channel_id or getattr(channel, "name", "") == "verify":
            return

        try:
            everyone = guild.default_role
            if isinstance(channel, discord.CategoryChannel):
                await self._lock_category(channel, everyone, unverified, verified)
            else:
                await self._apply_lock(channel, everyone, unverified, verified)
            logger.info(f"[Verification] Auto-locked new {type(channel).__name__}: {channel.name}")
        except Exception as e:
            logger.error(f"[Verification] Failed to auto-lock new channel {channel.name}: {e}")

    # ──────────────────────────────────────────────
    # Member join -> assign Unverified role
    # ──────────────────────────────────────────────

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        unverified, verified = self._get_roles(guild)

        if not unverified:
            logger.warning(f"[Verification] Unverified role not found in {guild.name}; skipping.")
            return

        # Skip if they somehow already have Verified
        if verified and verified in member.roles:
            return

        try:
            await member.add_roles(unverified, reason="New member — awaiting verification")
            logger.info(f"[Verification] Gave Unverified role to {member.display_name} in {guild.name}")
        except discord.Forbidden:
            logger.error(f"[Verification] Missing permissions to assign Unverified role in {guild.name}")
        except Exception as e:
            logger.error(f"[Verification] Error giving Unverified role: {e}")

    # ──────────────────────────────────────────────
    # Button press -> grant Verified role
    # ──────────────────────────────────────────────

    async def handle_verify(self, interaction: discord.Interaction):
        member = interaction.user
        guild = interaction.guild

        unverified, verified = self._get_roles(guild)

        if not verified:
            await interaction.response.send_message(
                "❌ Verification is not set up on this server yet. Please contact an admin.",
                ephemeral=True,
            )
            return

        if verified in member.roles:
            await interaction.response.send_message(
                "✅ You are already verified! Enjoy the server.",
                ephemeral=True,
            )
            return

        try:
            roles_to_remove = []
            if unverified and unverified in member.roles:
                roles_to_remove.append(unverified)

            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Verification complete")

            await member.add_roles(verified, reason="Member verified via button")

            embed = discord.Embed(
                title="✅ Verification Complete!",
                description=f"Welcome to **{guild.name}**! You now have access to all channels.",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"[Verification] Verified {member.display_name} in {guild.name}")

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to assign roles. Please contact an admin.",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"[Verification] Error during verification for {member.display_name}: {e}")
            await interaction.response.send_message(
                "❌ An error occurred during verification. Please try again or contact an admin.",
                ephemeral=True,
            )

    # ──────────────────────────────────────────────
    # Admin slash command handler
    # ──────────────────────────────────────────────

    async def setup_interaction(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Only administrators can run this command.", ephemeral=True
            )
            return

        guild = interaction.guild
        await interaction.response.send_message(
            "⏳ Setting up verification system… This may take a moment depending on server size.", ephemeral=True
        )

        try:
            unverified, verified = await self.setup(guild)

            embed = discord.Embed(
                title="✅ Verification System Ready",
                description=(
                    "All channels and categories have been made private.\n"
                    "A **#verify** channel has been created for new members.\n\n"
                    f"**Unverified Role:** {unverified.mention}\n"
                    f"**Verified Role:** {verified.mention}\n\n"
                    "New members will automatically receive the **Unverified** role and can only "
                    "see the #verify channel until they click the verify button."
                ),
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"[Verification] setup_interaction error: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Setup failed: {e}", ephemeral=True)

    # ──────────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────────

    def get_verify_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        channel_id = self._get_verify_channel_id(guild.id)
        if channel_id:
            return guild.get_channel(channel_id)
        return None
