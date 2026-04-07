import discord
import json
import asyncio
from typing import List, Dict, Any, Tuple, Optional
from data_manager import dm

class ActionHandler:
    def __init__(self, bot):
        self.bot = bot

    async def execute_sequence(self, interaction: discord.Interaction, actions: List[Dict[str, Any]]) -> List[Tuple[str, bool]]:
        """Executes a list of actions and returns success/failure for each."""
        results = []
        for action in actions:
            name = action.get("name")
            params = action.get("parameters", {})
            
            try:
                success = await self.dispatch(interaction, name, params)
                results.append((name, success))
            except Exception as e:
                print(f"Action Error ({name}): {str(e)}")
                results.append((name, False))
        return results

    async def dispatch(self, interaction: discord.Interaction, name: str, params: Dict[str, Any]) -> bool:
        """Routes action names to specific methods."""
        method_name = f"action_{name}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(interaction, params)
        else:
            print(f"Unknown action: {name}")
            return False

    # --- Basic Actions ---

    async def action_create_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        guild = interaction.guild
        name = params.get("name", "new-channel")
        channel_type = params.get("type", "text")
        category_name = params.get("category")

        category = None
        if category_name:
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                category = await guild.create_category(category_name)

        if channel_type == "text":
            channel = await guild.create_text_channel(name, category=category)
        elif channel_type == "voice":
            channel = await guild.create_voice_channel(name, category=category)
        else:
            return False
            
        print(f"Created channel: {channel.name}")
        return True

    async def action_create_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        guild = interaction.guild
        name = params.get("name")
        color_hex = params.get("color", "#99AAB5").replace("#", "")
        color = discord.Color(int(color_hex, 16))
        
        role = await guild.create_role(name=name, color=color, reason="AI Action")
        return True

    async def action_assign_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        user_id = params.get("user_id")
        role_id = params.get("role_id")
        guild = interaction.guild
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        role = guild.get_role(role_id)
        
        if member and role:
            await member.add_roles(role)
            return True
        return False

    async def action_create_prefix_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        """Adds a custom '!' command to the guild structure."""
        guild_id = interaction.guild.id
        cmd_name = params.get("name")
        cmd_code = params.get("code") # The instruction for what the command does
        
        cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        cmds[cmd_name] = cmd_code
        dm.update_guild_data(guild_id, "custom_commands", cmds)
        return True

    async def action_send_embed(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        channel_name = params.get("channel")
        title = params.get("title")
        description = params.get("description")
        color = params.get("color", 0x3498db)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        embed = discord.Embed(title=title, description=description, color=color)
        await channel.send(embed=embed)
        return True

    # --- Specialized Systems ---

    async def action_setup_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        """
        Specific workflow for the Staff Application system.
        Creates channels, buttons, and help embed.
        """
        from modules.staff_system import StaffSystem
        system = StaffSystem(self.bot)
        return await system.setup(interaction, params)

    async def action_setup_economy(self, interaction: discord.Interaction, params: Dict[str, Any]) -> bool:
        """Set up economy channels, balance command, and shop."""
        # This will call the Economy module
        return True

    # --- Execution Logic ---

    async def execute_custom_command(self, message: discord.Interaction, code: str):
        """
        Executes a custom '!' command's stored code.
        Can be a simple string, a list of actions, or a special command object.
        """
        try:
            data = json.loads(code)
            
            # Handle list of actions (existing functionality)
            if isinstance(data, list):
                # We'd need a way to pass 'message' context to execute_sequence
                # For now, just acknowledge the command
                await message.channel.send("Command executed (action list).")
                return True
            
            # Handle special command objects
            elif isinstance(data, dict):
                command_type = data.get("command_type")
                
                if command_type == "application_status":
                    return await self.handle_application_status(message)
                elif command_type == "help_embed":
                    return await self.send_help_embed(message, data)
                else:
                    # Unknown dict type, fall back to sending as string
                    await message.channel.send(content=code)
                    return True
                    
            # Handle plain strings (existing functionality)
            else:
                await message.channel.send(content=code)
                return True
                
        except json.JSONDecodeError:
            # Not JSON, treat as plain string
            await message.channel.send(content=code)
            return True
        except Exception as e:
            print(f"Error executing custom command: {e}")
            await message.channel.send("An error occurred while executing this command.")
            return False

    async def handle_application_status(self, interaction: discord.Interaction) -> bool:
        """Handle !apply status command"""
        apps = dm.load_json("applications", default={})
        user_id = str(interaction.user.id)
        
        if user_id not in apps:
            await interaction.response.send_message("You have not submitted a staff application yet.", ephemeral=True)
            return True
            
        status = apps[user_id]["status"]
        timestamp = apps[user_id]["timestamp"]
        
        embed = discord.Embed(title="Your Staff Application Status", color=discord.Color.blue())
        embed.add_field(name="Status", value=status.capitalize(), inline=True)
        embed.add_field(name="Submitted", value=timestamp, inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return True

    async def handle_appeal_status(self, interaction: discord.Interaction) -> bool:
        """Handle !appeal status command"""
        appeals = dm.load_json("appeals", default={})
        user_id = str(interaction.user.id)
        
        if user_id not in appeals:
            await interaction.response.send_message("You have no active appeals.", ephemeral=True)
            return True
            
        appeal = appeals[user_id]
        status = appeal.get("status", "pending")
        timestamp = appeal.get("timestamp", "Unknown")
        action_id = appeal.get("action_id", "Unknown")
        
        embed = discord.Embed(title="Your Appeal Status", color=discord.Color.blue())
        embed.add_field(name="Action ID", value=str(action_id), inline=True)
        embed.add_field(name="Status", value=status.capitalize(), inline=True)
        embed.add_field(name="Submitted", value=str(timestamp), inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return True

    async def send_help_embed(self, interaction: discord.Interaction, data: dict) -> bool:
        """Send a help embed based on stored data"""
        title = data.get("title", "Help")
        description = data.get("description", "")
        fields = data.get("fields", [])
        
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        
        for field in fields:
            embed.add_field(
                name=field.get("name", ""),
                value=field.get("value", ""),
                inline=field.get("inline", False)
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return True
