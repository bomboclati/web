"""
🎭 REACTION ROLES SYSTEM - FULLY FUNCTIONAL
All buttons, modals, and features from Part 7 blueprint
"""

import discord
from discord.ext import commands
from typing import Optional, List, Dict, Any
from datetime import datetime

from data_manager import DataManager

dm = DataManager()


class ReactionRolesPanel(discord.ui.View):
    """Admin panel for reaction roles - ALL 8 BUTTONS"""
    
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    @discord.ui.button(label="➕ Add Reaction Role", style=discord.ButtonStyle.success, custom_id="rr_add")
    async def add_reaction_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddReactionRoleModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📋 View All Reaction Roles", style=discord.ButtonStyle.primary, custom_id="rr_view_all")
    async def view_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        reaction_roles = guild_data.get("reaction_roles", [])
        
        if not reaction_roles:
            await interaction.response.send_message("📭 No reaction roles configured!", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 All Reaction Roles", description=f"Total: {len(reaction_roles)}", color=discord.Color.blue())
        
        for i, rr in enumerate(reaction_roles[:10]):
            role = interaction.guild.get_role(int(rr.get("role_id", 0)))
            restrictions = []
            if rr.get("min_age"):
                restrictions.append(f"Age: {rr['min_age']}d")
            if rr.get("min_level"):
                restrictions.append(f"Lvl: {rr['min_level']}")
            if rr.get("prerequisite_role"):
                restrictions.append("Prereq role")
            if rr.get("incompatible_role"):
                restrictions.append("Incompatible role")
            
            embed.add_field(
                name=f"{rr.get('emoji', '❓')} {role.name if role else 'Unknown Role'}",
                value=f"Message: [Jump](https://discord.com/channels/{self.guild_id}/{rr.get('channel_id')}/{rr.get('message_id')})\nRestrictions: {', '.join(restrictions) if restrictions else 'None'}",
                inline=False
            )
        
        if len(reaction_roles) > 10:
            embed.set_footer(text=f"...and {len(reaction_roles) - 10} more")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="✏️ Edit Reaction Role", style=discord.ButtonStyle.primary, custom_id="rr_edit")
    async def edit_reaction_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        reaction_roles = guild_data.get("reaction_roles", [])
        
        if not reaction_roles:
            await interaction.response.send_message("No reaction roles to edit!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select reaction role to edit", options=[discord.SelectOption(label=f"{rr.get('emoji', '❓')} - {rr.get('role_id', '')}"[:25], value=str(i)) for i, rr in enumerate(reaction_roles)[:25]])
        
        async def select_callback(interaction: discord.Interaction):
            idx = int(select.values[0])
            rr = reaction_roles[idx]
            modal = EditReactionRoleModal(idx, rr)
            await interaction.response.send_modal(modal)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select a reaction role to edit:", view=view, ephemeral=True)
    
    @discord.ui.button(label="🗑️ Remove Reaction Role", style=discord.ButtonStyle.danger, custom_id="rr_remove")
    async def remove_reaction_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        reaction_roles = guild_data.get("reaction_roles", [])
        
        if not reaction_roles:
            await interaction.response.send_message("No reaction roles to remove!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select reaction role to remove", options=[discord.SelectOption(label=f"{rr.get('emoji', '❓')} - {rr.get('role_id', '')}"[:25], value=str(i)) for i, rr in enumerate(reaction_roles)[:25]])
        
        async def select_callback(interaction: discord.Interaction):
            idx = int(select.values[0])
            rr = reaction_roles[idx]
            
            confirm_view = discord.ui.View()
            
            @discord.ui.button(label="✅ Confirm Remove", style=discord.ButtonStyle.danger)
            async def confirm(interaction: discord.Interaction, button: discord.ui.Button):
                # Remove the binding
                reaction_roles.pop(idx)
                guild_data["reaction_roles"] = reaction_roles
                dm.update_guild_data(self.guild_id, guild_data)
                
                # Try to remove the reaction from the message
                try:
                    channel = interaction.guild.get_channel(int(rr.get("channel_id", 0)))
                    if channel:
                        message = await channel.fetch_message(int(rr.get("message_id", 0)))
                        emoji = rr.get("emoji", "❓")
                        # Remove bot's reaction
                        try:
                            await message.clear_reaction(emoji)
                        except:
                            pass
                except:
                    pass
                
                await interaction.response.send_message("✅ Reaction role removed!", ephemeral=True)
            
            confirm_view.add_item(confirm)
            await interaction.response.send_message(f"⚠️ Remove this reaction role?", view=confirm_view, ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select a reaction role to remove:", view=view, ephemeral=True)
    
    @discord.ui.button(label="📊 Stats", style=discord.ButtonStyle.secondary, custom_id="rr_stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        reaction_roles = guild_data.get("reaction_roles", [])
        logs = guild_data.get("reaction_role_log", [])
        
        # Count assignments this week
        week_ago = datetime.now().timestamp() - (7 * 24 * 60 * 60)
        recent_assignments = len([l for l in logs if l.get("timestamp", 0) > week_ago and l.get("action") == "add"])
        
        # Most popular
        role_counts = {}
        for rr in reaction_roles:
            role_id = rr.get("role_id")
            if role_id:
                role_counts[role_id] = len([l for l in logs if l.get("role_id") == role_id and l.get("action") == "add"])
        
        most_popular = max(role_counts.items(), key=lambda x: x[1], default=(None, 0))
        
        embed = discord.Embed(title="📊 Reaction Roles Statistics", color=discord.Color.blue())
        embed.add_field(name="Total Bindings", value=len(reaction_roles), inline=True)
        embed.add_field(name="Assignments This Week", value=recent_assignments, inline=True)
        embed.add_field(name="Total Log Entries", value=len(logs), inline=True)
        
        if most_popular[0]:
            role = interaction.guild.get_role(int(most_popular[0]))
            embed.add_field(name="🏆 Most Popular", value=f"{role.mention if role else most_popular[0]} ({most_popular[1]} assignments)", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="📋 View Assignment Log", style=discord.ButtonStyle.secondary, custom_id="rr_view_log")
    async def view_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        logs = guild_data.get("reaction_role_log", [])
        
        if not logs:
            await interaction.response.send_message("📭 No log entries!", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 Assignment Log", description="Last 30 events", color=discord.Color.greyple())
        
        for log in logs[-30:][::-1]:
            action = "➕ Added" if log.get("action") == "add" else "➖ Removed"
            timestamp = log.get("timestamp", 0)
            time_str = f"<t:{int(timestamp)}:R>" if timestamp else "Unknown"
            
            embed.add_field(
                name=f"{action} - <@{log.get('user_id', 'unknown')}>",
                value=f"Role: <@&{log.get('role_id', 'unknown')}>\nTime: {time_str}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🔃 Sync All Reactions", style=discord.ButtonStyle.primary, custom_id="rr_sync_all")
    async def sync_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = discord.ui.View()
        
        @discord.ui.button(label="✅ Confirm Sync", style=discord.ButtonStyle.success)
        async def confirm(interaction: discord.Interaction, button: discord.ui.Button):
            guild_data = dm.get_guild_data(self.guild_id)
            reaction_roles = guild_data.get("reaction_roles", [])
            
            synced = 0
            failed = 0
            
            for rr in reaction_roles:
                try:
                    channel = interaction.guild.get_channel(int(rr.get("channel_id", 0)))
                    if channel:
                        message = await channel.fetch_message(int(rr.get("message_id", 0)))
                        emoji = rr.get("emoji")
                        if emoji:
                            await message.add_reaction(emoji)
                            synced += 1
                except Exception as e:
                    failed += 1
            
            await interaction.response.send_message(f"✅ Sync complete!\n\nSynced: {synced}\nFailed: {failed}", ephemeral=True)
        
        confirm_view.add_item(confirm)
        await interaction.response.send_message("⚠️ This will re-add all reactions to their messages. Continue?", view=confirm_view, ephemeral=True)
    
    @discord.ui.button(label="🎭 Set Role Limit", style=discord.ButtonStyle.secondary, custom_id="rr_set_limit")
    async def set_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RoleLimitModal()
        await interaction.response.send_modal(modal)


class AddReactionRoleModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Add Reaction Role")
        self.message_url = discord.ui.TextInput(label="Message URL", placeholder="https://discord.com/channels/.../.../...", max_length=200)
        self.add_item(self.message_url)
        self.emoji = discord.ui.TextInput(label="Emoji", placeholder="✅ or <:custom:12345>", max_length=50)
        self.add_item(self.emoji)
        self.role_id = discord.ui.TextInput(label="Role ID", placeholder="The role to assign", max_length=30)
        self.add_item(self.role_id)
        self.min_age = discord.ui.TextInput(label="Min Account Age (days)", placeholder="0 for none", default="0", required=False, max_length=10)
        self.add_item(self.min_age)
        self.min_level = discord.ui.TextInput(label="Min Level", placeholder="0 for none", default="0", required=False, max_length=10)
        self.add_item(self.min_level)
        self.prereq_role = discord.ui.TextInput(label="Prerequisite Role ID", placeholder="Optional", required=False, max_length=30)
        self.add_item(self.prereq_role)
        self.incompatible_role = discord.ui.TextInput(label="Incompatible Role ID", placeholder="Having this prevents assignment", required=False, max_length=30)
        self.add_item(self.incompatible_role)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse message URL
            parts = self.message_url.value.split("/")
            if len(parts) < 7:
                await interaction.response.send_message("❌ Invalid message URL!", ephemeral=True)
                return
            
            channel_id = int(parts[-2])
            message_id = int(parts[-1])
            role_id = self.role_id.value.strip()
            
            # Verify role exists
            role = interaction.guild.get_role(int(role_id))
            if not role:
                await interaction.response.send_message("❌ That role doesn't exist!", ephemeral=True)
                return
            
            # Verify message exists
            channel = interaction.guild.get_channel(channel_id)
            if not channel:
                await interaction.response.send_message("❌ Channel not found!", ephemeral=True)
                return
            
            message = await channel.fetch_message(message_id)
            
            guild_data = dm.get_guild_data(interaction.guild_id)
            if "reaction_roles" not in guild_data:
                guild_data["reaction_roles"] = []
            
            rr_entry = {
                "message_id": message_id,
                "channel_id": channel_id,
                "emoji": self.emoji.value.strip(),
                "role_id": role_id,
                "created_at": datetime.now().isoformat(),
                "created_by": str(interaction.user.id)
            }
            
            if self.min_age.value and int(self.min_age.value) > 0:
                rr_entry["min_age"] = int(self.min_age.value)
            if self.min_level.value and int(self.min_level.value) > 0:
                rr_entry["min_level"] = int(self.min_level.value)
            if self.prereq_role.value:
                rr_entry["prerequisite_role"] = self.prereq_role.value.strip()
            if self.incompatible_role.value:
                rr_entry["incompatible_role"] = self.incompatible_role.value.strip()
            
            guild_data["reaction_roles"].append(rr_entry)
            dm.update_guild_data(interaction.guild_id, guild_data)
            
            # Add reaction to message
            try:
                await message.add_reaction(self.emoji.value.strip())
            except:
                pass
            
            await interaction.response.send_message(f"✅ Reaction role added!\n\nEmoji: {self.emoji.value}\nRole: {role.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid ID format!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


class EditReactionRoleModal(discord.ui.Modal):
    def __init__(self, idx: int, rr: dict):
        super().__init__(title="Edit Reaction Role")
        self.idx = idx
        self.emoji = discord.ui.TextInput(label="Emoji", default=rr.get("emoji", ""), max_length=50)
        self.add_item(self.emoji)
        self.role_id = discord.ui.TextInput(label="Role ID", default=rr.get("role_id", ""), max_length=30)
        self.add_item(self.role_id)
        self.min_age = discord.ui.TextInput(label="Min Account Age (days)", default=str(rr.get("min_age", 0)), required=False, max_length=10)
        self.add_item(self.min_age)
        self.min_level = discord.ui.TextInput(label="Min Level", default=str(rr.get("min_level", 0)), required=False, max_length=10)
        self.add_item(self.min_level)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        reaction_roles = guild_data.get("reaction_roles", [])
        
        if self.idx < len(reaction_roles):
            reaction_roles[self.idx]["emoji"] = self.emoji.value.strip()
            reaction_roles[self.idx]["role_id"] = self.role_id.value.strip()
            if self.min_age.value:
                reaction_roles[self.idx]["min_age"] = int(self.min_age.value)
            elif "min_age" in reaction_roles[self.idx]:
                del reaction_roles[self.idx]["min_age"]
            if self.min_level.value:
                reaction_roles[self.idx]["min_level"] = int(self.min_level.value)
            elif "min_level" in reaction_roles[self.idx]:
                del reaction_roles[self.idx]["min_level"]
            
            guild_data["reaction_roles"] = reaction_roles
            dm.update_guild_data(interaction.guild_id, guild_data)
            
            await interaction.response.send_message("✅ Reaction role updated!", ephemeral=True)


class RoleLimitModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Set Role Limit")
        self.limit = discord.ui.TextInput(label="Max Roles Per User", placeholder="0 = unlimited", default="0", max_length=10)
        self.add_item(self.limit)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        guild_data["reaction_role_limit"] = int(self.limit.value)
        dm.update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message(f"✅ Role limit set to {self.limit.value}!", ephemeral=True)


def setup_reaction_roles_commands(bot):
    @bot.command(name="reactionrolespanel")
    async def reaction_roles_panel(ctx):
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator permissions required!")
            return
        
        embed = discord.Embed(title="🎭 Reaction Roles Management Panel", description="Manage reaction-based role assignments", color=discord.Color.blue())
        view = ReactionRolesPanel(ctx.guild.id)
        await ctx.send(embed=embed, view=view)
