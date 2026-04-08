import discord
import json
import asyncio
import time
import datetime
from typing import List, Dict, Any, Tuple, Optional
from data_manager import dm
from logger import logger

class ActionHandler:
    def __init__(self, bot):
        self.bot = bot
        self._action_log = []
        self._setup_id = None
        self._artifacts = []

    async def execute_sequence(self, interaction: discord.Interaction, actions: List[Dict[str, Any]], auto_rollback: bool = True) -> Dict[str, Any]:
        """Executes a list of actions with automatic rollback on failure and crash recovery tracking."""
        import uuid
        setup_id = str(uuid.uuid4())
        results = []
        self._action_log = []
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups[setup_id] = {
            "guild_id": guild_id,
            "user_id": user_id,
            "actions_count": len(actions),
            "actions_taken": [],
            "started_at": time.time()
        }
        dm.save_json("pending_setups", pending_setups)
        self._setup_id = setup_id
        self._artifacts = []

        for i, action in enumerate(actions):
            name = action.get("name")
            params = action.get("parameters", {})
            
            try:
                success, undo_data = await self.dispatch(interaction, name, params)
                results.append((name, success))
                
                if success and undo_data:
                    self._action_log.append({
                        "action": name,
                        "undo_data": undo_data,
                        "guild_id": guild_id,
                        "user_id": user_id,
                        "timestamp": time.time()
                    })
                elif not success:
                    raise Exception(f"Action returned failure: {name}")
            except Exception as e:
                error_msg = str(e)
                logger.error("Action Error (%s): %s", name, error_msg)
                results.append((name, False))
                
                self._record_failure(guild_id, name, error_msg)
                
                if auto_rollback and self._action_log:
                    rollback_results = await self._rollback_sequence(interaction)
                    return {
                        "results": results,
                        "rolled_back": rollback_results,
                        "failed_at": i,
                        "failed_action": name,
                        "error": error_msg,
                        "success": False
                    }
                else:
                    return {
                        "results": results,
                        "rolled_back": [],
                        "failed_at": i,
                        "failed_action": name,
                        "error": error_msg,
                        "success": False
                    }

        if self._action_log:
            action_logs = dm.get_guild_data(guild_id, "action_logs", [])
            action_logs.extend(self._action_log)
            dm.update_guild_data(guild_id, "action_logs", action_logs)

        self._record_successes(guild_id, [a.get("name") for a in actions])
        
        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups.pop(setup_id, None)
        dm.save_json("pending_setups", pending_setups)

        return {
            "results": results,
            "rolled_back": [],
            "failed_at": None,
            "success": True
        }

    async def _rollback_sequence(self, interaction: discord.Interaction) -> List[Tuple[str, bool]]:
        """Rollback all successfully executed actions in reverse order."""
        rollback_results = []
        for log_entry in reversed(self._action_log):
            undo_data = log_entry.get("undo_data", {})
            undo_action = undo_data.get("action")
            success = await self._execute_undo(interaction, undo_action, undo_data)
            rollback_results.append((log_entry.get("action", "unknown"), success))
        
        self._action_log = []
        return rollback_results

    def _record_failure(self, guild_id: int, action_name: str, error: str):
        """Record action failure for AI self-improvement."""
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        if action_name not in failures:
            failures[action_name] = {"count": 0, "errors": [], "last_error": None}
        failures[action_name]["count"] += 1
        failures[action_name]["last_error"] = error[:200]
        if len(failures[action_name]["errors"]) < 10:
            failures[action_name]["errors"].append(error[:200])
        dm.update_guild_data(guild_id, "action_failures", failures)
        
        dm.record_global_action_result(action_name, False, error)

    def _record_successes(self, guild_id: int, action_names: List[str]):
        """Record action successes for AI self-improvement."""
        successes = dm.get_guild_data(guild_id, "action_successes", {})
        for name in action_names:
            if name not in successes:
                successes[name] = 0
            successes[name] += 1
        dm.update_guild_data(guild_id, "action_successes", successes)
        
        for name in action_names:
            dm.record_global_action_result(name, True)

    def _track_artifact(self, artifact_type: str, artifact_id: int, name: str):
        """Track a created artifact for crash recovery."""
        if self._setup_id:
            self._artifacts.append({"type": artifact_type, "id": artifact_id, "name": name})
            pending_setups = dm.load_json("pending_setups", default={})
            if self._setup_id in pending_setups:
                pending_setups[self._setup_id]["actions_taken"].append(
                    {"type": artifact_type, "id": artifact_id, "name": name}
                )
                dm.save_json("pending_setups", pending_setups)

    async def dispatch(self, interaction: discord.Interaction, name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Routes action names to specific methods. Returns (success, undo_data)."""
        method_name = f"action_{name}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(interaction, params)
        else:
            logger.warning("Unknown action: %s", name)
            return False, None

    # --- Basic Actions ---

    async def action_create_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
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
            return False, None
            
        self._track_artifact("channel", channel.id, channel.name)
        logger.info("Created channel: %s", channel.name)
        return True, {"action": "delete_channel", "channel_id": channel.id}

    async def action_create_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = params.get("name")
        color_hex = params.get("color", "#99AAB5").replace("#", "")
        color = discord.Color(int(color_hex, 16))
        
        role = await guild.create_role(name=name, color=color, reason="AI Action")
        self._track_artifact("role", role.id, role.name)
        return True, {"action": "delete_role", "role_id": role.id}

    async def action_assign_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = params.get("user_id")
        role_id = params.get("role_id")
        guild = interaction.guild
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        role = guild.get_role(role_id)
        
        if member and role:
            await member.add_roles(role)
            return True, {"action": "remove_role", "user_id": user_id, "role_id": role_id}
        return False, None

    async def action_create_prefix_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds a custom '!' command to the guild structure."""
        guild_id = interaction.guild.id
        cmd_name = params.get("name")
        cmd_code = params.get("code")
        
        cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        existing = cmds.get(cmd_name)
        cmds[cmd_name] = cmd_code
        dm.update_guild_data(guild_id, "custom_commands", cmds)
        return True, {"action": "delete_prefix_command", "cmd_name": cmd_name, "previous_code": existing}

    async def action_send_embed(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channel_name = params.get("channel")
        title = params.get("title")
        description = params.get("description")
        color = params.get("color", 0x3498db)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        embed = discord.Embed(title=title, description=description, color=color)
        msg = await channel.send(embed=embed)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    async def action_post_documentation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Posts a comprehensive, multi-section documentation embed for a newly created system."""
        channel_name = params.get("channel")
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        
        title = params.get("title", "System Documentation")
        description = params.get("description", "")
        sections = params.get("sections", [])
        footer = params.get("footer", "")
        color = params.get("color", 0x5865F2)
        
        embed = discord.Embed(title=title, description=description, color=color)
        
        for section in sections:
            section_title = section.get("title", "")
            section_content = section.get("content", "")
            if section_title and section_content:
                embed.add_field(name=section_title, value=section_content, inline=False)
        
        if footer:
            embed.set_footer(text=footer)
        
        embed.timestamp = datetime.datetime.utcnow()
        
        msg = await channel.send(embed=embed)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    # --- Specialized Systems ---

    async def action_setup_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.staff_system import StaffSystem
        system = StaffSystem(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_staff_system", "guild_id": interaction.guild.id}

    async def action_setup_economy(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.economy import Economy
        system = Economy(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_economy", "guild_id": interaction.guild.id}

    async def action_setup_trigger_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.trigger_roles import TriggerRoles
        system = TriggerRoles(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_trigger_role", "guild_id": interaction.guild.id}

    # --- Execution Logic ---

    async def execute_custom_command(self, message: discord.Interaction, code: str, cmd_name: str = None):
        """
        Executes a custom '!' command's stored code.
        Can be a simple string, a list of actions, or a special command object.
        Includes error prevention based on learned patterns.
        """
        guild_id = message.guild.id
        cmd_data_obj = None
        
        try:
            data = json.loads(code)
            cmd_data_obj = data
            
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
                elif command_type == "appeal_status":
                    return await self.handle_appeal_status(message)
                elif command_type == "help_embed":
                    return await self.send_help_embed(message, data)
                elif command_type == "list_triggers":
                    return await self.list_triggers(message)
                elif command_type == "economy_daily":
                    return await self.handle_economy_daily(message)
                elif command_type == "economy_balance":
                    return await self.handle_economy_balance(message)
                elif command_type == "list_achievements":
                    return await self.handle_list_achievements(message)
                elif command_type == "list_titles":
                    return await self.handle_list_titles(message)
                elif command_type == "set_title":
                    return await self.handle_set_title(message)
                elif command_type == "achievements_leaderboard":
                    return await self.handle_achievements_leaderboard(message)
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
            logger.error("Error executing custom command: %s", e)
            
            # Check for error prevention
            prevention = self._get_error_prevention(guild_id, cmd_name, cmd_data_obj, str(e))
            if prevention:
                await message.channel.send(prevention.get("message", "An error occurred. Try: !help " + (cmd_name or "help")))
            else:
                await message.channel.send("An error occurred while executing this command.")
            return False
    
    def _get_error_prevention(self, guild_id: int, cmd_name: str, cmd_data: dict, error_msg: str) -> dict:
        """Get error prevention measures based on learned patterns."""
        if not cmd_name:
            return None
        
        # Check if command has pre-configured prevention
        if cmd_data and isinstance(cmd_data, dict):
            prevention = cmd_data.get("error_prevention")
            if prevention and prevention.get("prevention_enabled"):
                return prevention
        
        # Check stored prevention from AI analysis
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        if cmd_name in custom_cmds:
            stored = custom_cmds[cmd_name]
            try:
                stored_data = json.loads(stored) if isinstance(stored, str) else stored
                if isinstance(stored_data, dict):
                    prevention = stored_data.get("error_prevention")
                    if prevention and prevention.get("prevention_enabled"):
                        return prevention
            except:
                pass
        
        return None

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

    async def list_triggers(self, interaction: discord.Interaction) -> bool:
        """List all active trigger words for the guild"""
        guild_id = interaction.guild.id
        triggers = dm.get_guild_data(guild_id, "trigger_roles", {})
        
        if not triggers:
            await interaction.response.send_message("No trigger words are currently set up.", ephemeral=True)
            return True
            
        embed = discord.Embed(title="Active Trigger Words", color=discord.Color.blue())
        
        for word, role_id in triggers.items():
            role = interaction.guild.get_role(role_id)
            role_name = role.name if role else f"Unknown Role (ID: {role_id})"
            embed.add_field(
                name=f"Trigger: `{word}`",
                value=f"Assigns role: **{role_name}**",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return True

    async def handle_economy_daily(self, message: discord.Message) -> bool:
        """Handle !daily command"""
        from modules.economy import Economy
        economy = Economy(self.bot)
        guild_id = message.guild.id
        user_id = message.author.id
        
        last_daily = dm.get_guild_data(guild_id, "last_daily", {})
        last_time = last_daily.get(str(user_id))
        
        if last_time:
            last_date = datetime.datetime.fromisoformat(last_time)
            if (datetime.datetime.now() - last_date).days < 1:
                await message.channel.send("Daily reward already claimed today!")
                return True
        
        reward = 100
        economy.add_coins(guild_id, user_id, reward)
        last_daily[str(user_id)] = str(datetime.datetime.now())
        dm.update_guild_data(guild_id, "last_daily", last_daily)
        
        await message.channel.send(f"💰 {message.author.mention} claimed **{reward} coins**!")
        return True

    async def handle_economy_balance(self, message: discord.Message) -> bool:
        """Handle !balance command"""
        from modules.economy import Economy
        from modules.leveling import Leveling
        economy = Economy(self.bot)
        leveling = Leveling(self.bot)
        guild_id = message.guild.id
        user_id = message.author.id
        
        coins = economy.get_coins(guild_id, user_id)
        gems = leveling.get_gems(guild_id, user_id)
        xp = leveling.get_xp(guild_id, user_id)
        level = leveling.get_level_from_xp(xp)
        
        embed = discord.Embed(title=f"{message.author.name}'s Balance", color=discord.Color.gold())
        embed.add_field(name="Coins", value=str(coins), inline=True)
        embed.add_field(name="Gems", value=str(gems), inline=True)
        embed.add_field(name="Level", value=f"{level} ({xp} XP)", inline=True)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_list_achievements(self, message: discord.Message) -> bool:
        """Handle !achievements command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        user_achievements = achievements.get_user_achievements(message.guild.id, message.author.id)
        
        if not user_achievements:
            embed = discord.Embed(
                title="🎯 Your Achievements",
                description="No achievements yet! Keep being active to earn some.",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title=f"🎯 {message.author.display_name}'s Achievements",
                description=f"**{len(user_achievements)} achievements earned**",
                color=discord.Color.gold()
            )
            
            # Group by category
            by_category = {}
            for ach in user_achievements:
                cat = ach.get("category", "other")
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(ach)
            
            for category, achs in by_category.items():
                ach_list = "\n".join([f"{a['icon']} **{a['name']}** - {a['description']}" for a in achs[:5]])
                if ach_list:
                    embed.add_field(
                        name=f"{category.title()} ({len(achs)})",
                        value=ach_list,
                        inline=False
                    )
        
        await message.channel.send(embed=embed)
        return True

    async def handle_list_titles(self, message: discord.Message) -> bool:
        """Handle !titles command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        user_titles = achievements.get_user_titles(message.guild.id, message.author.id)
        active_title = achievements.get_active_title(message.guild.id, message.author.id)
        
        if not user_titles:
            embed = discord.Embed(
                title="🎖️ Your Titles",
                description="No titles yet! Keep being active to unlock titles.",
                color=discord.Color.gold()
            )
        else:
            active_name = f"{active_title['icon']} {active_title['name']}" if active_title else "None"
            
            embed = discord.Embed(
                title=f"🎖️ {message.author.display_name}'s Titles",
                description=f"Active: **{active_name}**\n" +
                           f"Total unlocked: **{len(user_titles)}**",
                color=discord.Color.gold()
            )
            
            titles_list = "\n".join([f"{t['icon']} **{t['name']}**" for t in user_titles])
            embed.add_field(name="Unlocked Titles", value=titles_list, inline=False)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_set_title(self, message: discord.Message) -> bool:
        """Handle !settitle command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        # Parse command - get title from message content
        content = message.content.split(" ", 1)
        title_input = content[1].strip() if len(content) > 1 else None
        
        if not title_input:
            user_titles = achievements.get_user_titles(message.guild.id, message.author.id)
            
            if not user_titles:
                await message.channel.send("You don't have any titles yet!")
                return True
            
            titles_list = "\n".join([f"• {t['icon']} **{t['name']}**" for t in user_titles])
            await message.channel.send(f"Available titles:\n{titles_list}\n\nUse `!settitle <name>` to set one.")
            return True
        
        # Find matching title
        user_titles = achievements.get_user_titles(message.guild.id, message.author.id)
        
        matched_title = None
        for t in user_titles:
            if title_input.lower() in t['name'].lower() or title_input.lower() == t['id'].lower():
                matched_title = t
                break
        
        if not matched_title:
            await message.channel.send(f"No title matching '{title_input}' found. Use `!titles` to see your titles.")
            return True
        
        # Set active title
        achievements.set_active_title(message.guild.id, message.author.id, matched_title['id'])
        
        await message.channel.send(f"✅ Title set to **{matched_title['icon']} {matched_title['name']}**!")
        return True

    async def handle_achievements_leaderboard(self, message: discord.Message) -> bool:
        """Handle !achievementsleaderboard command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        leaderboard = achievements.get_leaderboard(message.guild.id)
        
        if not leaderboard:
            await message.channel.send("No achievements recorded yet!")
            return True
        
        embed = discord.Embed(
            title="🏆 Achievement Leaderboard",
            description="Top achievement collectors in this server:",
            color=discord.Color.gold()
        )
        
        lb_text = "\n".join([
            f"**{entry['rank']}.** <@{entry['user_id']}> - {entry['achievements']} achievements"
            for entry in leaderboard
        ])
        
        embed.add_field(name="Rankings", value=lb_text, inline=False)
        
        await message.channel.send(embed=embed)
        return True

    async def undo_last_actions(self, interaction: discord.Interaction, count: int = 1) -> List[Tuple[str, bool]]:
        """Undo the last N action groups for the guild."""
        guild_id = interaction.guild.id
        action_logs = dm.get_guild_data(guild_id, "action_logs", [])
        
        if not action_logs:
            return [("No actions to undo", False)]
        
        to_undo = action_logs[-count:]
        remaining = action_logs[:-count]
        dm.update_guild_data(guild_id, "action_logs", remaining)
        
        results = []
        for log_entry in reversed(to_undo):
            undo_data = log_entry.get("undo_data", {})
            undo_action = undo_data.get("action")
            success = await self._execute_undo(interaction, undo_action, undo_data)
            results.append((log_entry.get("action", "unknown"), success))
        
        return results

    async def _execute_undo(self, interaction: discord.Interaction, undo_action: str, undo_data: Dict) -> bool:
        """Execute a specific undo operation."""
        try:
            guild = interaction.guild
            
            if undo_action == "delete_channel":
                channel = guild.get_channel(undo_data.get("channel_id"))
                if channel:
                    await channel.delete()
                    return True
                    
            elif undo_action == "delete_role":
                role = guild.get_role(undo_data.get("role_id"))
                if role:
                    await role.delete()
                    return True
                    
            elif undo_action == "remove_role":
                member = guild.get_member(undo_data.get("user_id"))
                role = guild.get_role(undo_data.get("role_id"))
                if member and role:
                    await member.remove_roles(role)
                    return True
                    
            elif undo_action == "delete_prefix_command":
                cmd_name = undo_data.get("cmd_name")
                cmds = dm.get_guild_data(guild.id, "custom_commands", {})
                if cmd_name in cmds:
                    previous = undo_data.get("previous_code")
                    if previous:
                        cmds[cmd_name] = previous
                    else:
                        del cmds[cmd_name]
                    dm.update_guild_data(guild.id, "custom_commands", cmds)
                    return True
                    
            elif undo_action == "delete_message":
                channel = guild.get_channel(undo_data.get("channel_id"))
                if channel:
                    try:
                        msg = await channel.fetch_message(undo_data.get("message_id"))
                        await msg.delete()
                        return True
                    except discord.NotFound:
                        pass
                        
            elif undo_action in ("undo_staff_system", "undo_economy", "undo_trigger_role"):
                return await self._undo_system_setup(guild, undo_action)
                
            return False
        except Exception as e:
            logger.error("Undo Error (%s): %s", undo_action, e)
            return False

    async def _undo_system_setup(self, guild: discord.Guild, undo_action: str) -> bool:
        """Undo a system setup by removing associated channels and commands."""
        try:
            if undo_action == "undo_staff_system":
                channels_to_remove = ["apply-staff", "apply-staff-logs"]
                cmds_to_remove = ["apply", "help staffapply"]
            elif undo_action == "undo_economy":
                channels_to_remove = ["economy", "shop"]
                cmds_to_remove = ["daily", "balance", "transfer", "shop", "help economy"]
            elif undo_action == "undo_trigger_role":
                channels_to_remove = []
                cmds_to_remove = ["triggers", "help triggers"]
            else:
                return False
            
            for ch_name in channels_to_remove:
                channel = discord.utils.get(guild.channels, name=ch_name)
                if channel:
                    await channel.delete()
            
            cmds = dm.get_guild_data(guild.id, "custom_commands", {})
            for cmd in cmds_to_remove:
                cmds.pop(cmd, None)
            dm.update_guild_data(guild.id, "custom_commands", cmds)
            
            return True
        except Exception as e:
            logger.error("System Undo Error (%s): %s", undo_action, e)
            return False
