import discord
from discord import ui
import time
from typing import Dict, List, Optional, Any, Union
from data_manager import dm
from logger import logger

class RoleButtonPersistentView(ui.View):
    """
    Global persistent view for all standalone role buttons.
    Uses custom_id parsing to identify the panel and button.
    Pattern: role_btn_{panel_id}_{button_id}
    """
    def __init__(self):
        super().__init__(timeout=None)

    async def _get_panel_data(self, guild_id: int, panel_id: str):
        panels = dm.get_guild_data(guild_id, "role_buttons_config", {})
        return panels.get(panel_id)

    @ui.button(label="Placeholder", custom_id="role_btn_global_stub")
    async def stub_button(self, interaction: discord.Interaction, button: ui.Button):
        # This is never actually called because we use dynamic custom_ids
        pass

    async def handle_interaction(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("role_btn_"):
            return False

        # Parse role_btn_{panel_id}_{button_id}
        parts = custom_id.split("_")
        if len(parts) < 4:
            return False

        panel_id = f"{parts[2]}_{parts[3]}" # Reconstruct panel_id (panel_timestamp)
        button_id = "_".join(parts[4:]) if len(parts) > 4 else ""

        guild = interaction.guild
        panel_data = await self._get_panel_data(guild.id, panel_id)
        
        if not panel_data:
            # Try to find panel_id if it's not in the simple format
            # This handles cases where panel_id might contain underscores itself
            for pid, pdata in dm.get_guild_data(guild.id, "role_buttons_config", {}).items():
                if custom_id.startswith(f"role_btn_{pid}_"):
                    panel_id = pid
                    panel_data = pdata
                    button_id = custom_id.replace(f"role_btn_{pid}_", "")
                    break

        if not panel_data or not panel_data.get("enabled", True):
            await interaction.response.send_message("⚠️ This panel is currently unavailable.", ephemeral=True)
            return True

        button_data = panel_data.get("buttons", {}).get(button_id)
        if not button_data:
            await interaction.response.send_message("❌ Button configuration not found.", ephemeral=True)
            return True

        # Check requirements
        req = button_data.get("requirement", {})
        if req.get("role_id"):
            if not any(r.id == int(req["role_id"]) for r in interaction.user.roles):
                await interaction.response.send_message("❌ You don't meet the role requirement for this.", ephemeral=True)
                return True

        try:
            role_to_add = guild.get_role(int(button_data["role_id"]))
            role_to_remove = guild.get_role(int(button_data["remove_role_id"])) if button_data.get("remove_role_id") else None

            if not role_to_add:
                await interaction.response.send_message("❌ Role to assign not found. Please contact an administrator.", ephemeral=True)
                return True

            if role_to_add in interaction.user.roles:
                await interaction.user.remove_roles(role_to_add, reason="Role Button Toggle")
                await interaction.response.send_message(f"✅ Removed role: **{role_to_add.name}**", ephemeral=True)
                action = "remove"
            else:
                if role_to_remove:
                    await interaction.user.remove_roles(role_to_remove, reason="Role Button Swap")
                await interaction.user.add_roles(role_to_add, reason="Role Button Assignment")
                await interaction.response.send_message(f"✅ Added role: **{role_to_add.name}**", ephemeral=True)
                action = "add"

            # Log click
            log = dm.get_guild_data(guild.id, "role_button_log", [])
            log.append({
                "ts": time.time(),
                "user_id": interaction.user.id,
                "role_id": role_to_add.id,
                "action": action,
                "panel_name": panel_data.get("title"),
                "button_label": button_data.get("label")
            })
            dm.update_guild_data(guild.id, "role_button_log", log[-100:])

            # Increment stats
            panel_data["total_clicks"] = panel_data.get("total_clicks", 0) + 1
            panels = dm.get_guild_data(guild.id, "role_buttons_config", {})
            panels[panel_id] = panel_data
            dm.update_guild_data(guild.id, "role_buttons_config", panels)

        except Exception as e:
            logger.error(f"Failed to process role button click: {e}")
            await interaction.response.send_message("❌ An error occurred during role assignment.", ephemeral=True)
        
        return True

class RoleButtons(commands.Cog):
    """
    Role Buttons System:
    Standalone role assignment buttons - simpler than menus.
    """
    def __init__(self, bot):
        self.bot = bot
        self.persistent_view = RoleButtonPersistentView()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
            
        custom_id = interaction.data.get("custom_id", "")
        if custom_id and custom_id.startswith("role_btn_"):
            await self.persistent_view.handle_interaction(interaction)

    def get_panels(self, guild_id: int) -> Dict[str, Any]:
        return dm.get_guild_data(guild_id, "role_buttons_config", {})

    def save_panels(self, guild_id: int, panels: Dict[str, Any]):
        dm.update_guild_data(guild_id, "role_buttons_config", panels)

    async def create_panel(self, interaction: discord.Interaction, title: str, description: str, channel: discord.TextChannel):
        guild_id = interaction.guild_id
        panels = self.get_panels(guild_id)

        panel_id = f"panel_{int(time.time())}"

        panel_data = {
            "id": panel_id,
            "title": title,
            "description": description,
            "channel_id": channel.id,
            "buttons": {}, # button_id -> data
            "enabled": True,
            "total_clicks": 0,
            "created_at": time.time()
        }

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        try:
            message = await channel.send(embed=embed)
            panel_data["message_id"] = message.id
            panels[panel_id] = panel_data
            self.save_panels(guild_id, panels)
            return panel_id
        except Exception as e:
            logger.error(f"Failed to create role button panel: {e}")
            return None

    def build_view(self, panel_id: str, buttons_data: Dict[str, Any]) -> ui.View:
        view = ui.View(timeout=None)

        for bid, data in buttons_data.items():
            btn = ui.Button(
                label=data.get("label", "Role"),
                style=getattr(discord.ButtonStyle, data.get("style", "secondary")),
                emoji=data.get("emoji"),
                custom_id=f"role_btn_{panel_id}_{bid}"
            )
            view.add_item(btn)

        return view

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        """Setup for role buttons"""
        guild = interaction.guild
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["rolebuttonspanel"] = "configpanel rolebuttons"
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

        await interaction.followup.send("🔘 Role Buttons system initialized. Use `!rolebuttonspanel` to create a button panel.", ephemeral=True)
        return True

async def setup(bot):
    await bot.add_cog(RoleButtons(bot))
