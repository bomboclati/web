class AppealsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "appeals")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_appeals_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="⚖️ Appeals System",
            color=discord.Color.gold() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Appeal Channel", value=f"<#{c.get('appeal_channel_id')}>" if c.get("appeal_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="⚖️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_appeals_toggle")
    async def toggle_appeals(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_appeals_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_appeals_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["appeal_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Appeal channel set to current channel.", ephemeral=True)


class ModmailConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "modmail")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_modmail_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="📧 Modmail System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Modmail Category", value=f"<#{c.get('modmail_category_id')}>" if c.get("modmail_category_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📧", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_modmail_toggle")
    async def toggle_modmail(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_modmail_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class SuggestionsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "suggestions")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_suggestions_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="💡 Suggestions System",
            color=discord.Color.green() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Suggestions Channel", value=f"<#{c.get('suggestions_channel_id')}>" if c.get("suggestions_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="💡", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_suggestions_toggle")
    async def toggle_suggestions(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_suggestions_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class GiveawayConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "giveaway")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_giveaway_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="🎉 Giveaway System",
            color=discord.Color.magenta() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Giveaway Channel", value=f"<#{c.get('giveaway_channel_id')}>" if c.get("giveaway_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="🎉", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_giveaway_toggle")
    async def toggle_giveaway(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_giveaway_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class GamificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "gamification")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_gamification_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="🎮 Gamification System",
            color=discord.Color.orange() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Points Name", value=c.get("points_name", "Points"), inline=True)
        return embed

    @ui.button(label="Disable", emoji="🎮", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_gamification_toggle")
    async def toggle_gamification(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_gamification_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class ReactionRolesConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reactionroles")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_reactionroles_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="🔄 Reaction Roles System",
            color=discord.Color.teal() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Reaction Roles Count", value=str(len(c.get("reaction_roles", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="🔄", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_reactionroles_toggle")
    async def toggle_reactionroles(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_reactionroles_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class ReactionMenusConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reactionmenus")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_reactionmenus_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="📋 Reaction Menus System",
            color=discord.Color.purple() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Menus Count", value=str(len(c.get("menus", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="📋", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_reactionmenus_toggle")
    async def toggle_reactionmenus(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_reactionmenus_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class RoleButtonsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "rolebuttons")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_rolebuttons_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="🔘 Role Buttons System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Buttons Count", value=str(len(c.get("buttons", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="🔘", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_rolebuttons_toggle")
    async def toggle_rolebuttons(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_rolebuttons_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class ModLogConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "modlog")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_modlog_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="📜 Mod Log System",
            color=discord.Color.red() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('modlog_channel_id')}>" if c.get("modlog_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📜", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_modlog_toggle")
    async def toggle_modlog(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_modlog_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_modlog_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["modlog_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Mod log channel set to current channel.", ephemeral=True)


class LoggingConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "logging")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_logging_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="📝 Logging System",
            color=discord.Color.dark_grey() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('logging_channel_id')}>" if c.get("logging_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📝", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_logging_toggle")
    async def toggle_logging(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_logging_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_logging_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["logging_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Logging channel set to current channel.", ephemeral=True)


class StaffPromoConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staffpromo")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_staffpromo_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="🎖️ Staff Promo System",
            color=discord.Color.gold() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Promo Role", value=f"<@&{c.get('promo_role_id')}>" if c.get("promo_role_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="🎖️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_staffpromo_toggle")
    async def toggle_staffpromo(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_staffpromo_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
