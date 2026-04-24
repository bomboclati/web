import discord
from discord.ext import commands
import asyncio
import json
import time
import random
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

from data_manager import dm
from logger import logger
import os


@dataclass
class Giveaway:
    id: str
    guild_id: int
    channel_id: int
    message_id: Optional[int]
    name: str
    description: str
    prize: str
    winners_count: int
    requirements: dict
    ends_at: float
    entries: List[int]
    winners: List[int]
    created_by: int
    created_at: float
    ended: bool


class GiveawaySystem:
    def __init__(self, bot):
        self.bot = bot
        self._giveaways: Dict[str, Giveaway] = {}
        self._load_giveaways()

    def _load_giveaways(self):
        """Load giveaways from all guild-specific files."""
        count = 0
        data_dir = "data"
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_id_str = filename[6:-5]
                        if not guild_id_str.isdigit(): continue
                        guild_id = int(guild_id_str)
                        guild_data = dm.load_json(filename[:-5], default={})
                        giveaways_data = guild_data.get("giveaways", {})

                        for gw_id, gw_data in giveaways_data.items():
                            giveaway = Giveaway(
                                id=gw_id,
                                guild_id=guild_id,
                                channel_id=gw_data["channel_id"],
                                message_id=gw_data.get("message_id"),
                                name=gw_data["name"],
                                description=gw_data["description"],
                                prize=gw_data["prize"],
                                winners_count=gw_data["winners_count"],
                                requirements=gw_data.get("requirements", {}),
                                ends_at=gw_data["ends_at"],
                                entries=gw_data.get("entries", []),
                                winners=gw_data.get("winners", []),
                                created_by=gw_data["created_by"],
                                created_at=gw_data["created_at"],
                                ended=gw_data.get("ended", False)
                            )
                            if not giveaway.ended:
                                self._giveaways[gw_id] = giveaway
                                count += 1
                    except Exception as e:
                        logger.error(f"Failed to load giveaways from {filename}: {e}")
        logger.info(f"Loaded {count} active/recent giveaways from guild files.")

    def _save_giveaway(self, giveaway: Giveaway):
        """Save a giveaway to its guild-specific data file."""
        guild_id = giveaway.guild_id
        giveaways = dm.get_guild_data(guild_id, "giveaways", {})

        giveaways[giveaway.id] = {
            "channel_id": giveaway.channel_id,
            "message_id": giveaway.message_id,
            "name": giveaway.name,
            "description": giveaway.description,
            "prize": giveaway.prize,
            "winners_count": giveaway.winners_count,
            "requirements": giveaway.requirements,
            "ends_at": giveaway.ends_at,
            "entries": giveaway.entries,
            "winners": giveaway.winners,
            "created_by": giveaway.created_by,
            "created_at": giveaway.created_at,
            "ended": giveaway.ended
        }
        dm.update_guild_data(guild_id, "giveaways", giveaways)

    def start_giveaway_monitor(self):
        asyncio.create_task(self._giveaway_monitor_loop())

    async def _giveaway_monitor_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed:
            try:
                current_time = time.time()
                
                for gw_id, giveaway in list(self._giveaways.items()):
                    if giveaway.ends_at <= current_time and not giveaway.ended:
                        await self.end_giveaway(gw_id)
            except Exception as e:
                logger.error(f"Giveaway monitor error: {e}")
            
            await asyncio.sleep(30)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "giveaway_settings", {
            "enabled": True,
            "default_winners": 1,
            "default_duration_hours": 24,
            "emoji": "🎉",
            "entry_dms": True,
            "bonus_roles": {}
        })

    async def create_giveaway(self, guild_id: int, channel_id: int, creator_id: int,
                           name: str, description: str, prize: str, winners_count: int,
                           requirements: dict, duration_hours: int) -> Giveaway:
        giveaway_id = f"giveaway_{guild_id}_{int(time.time())}"
        
        giveaway = Giveaway(
            id=giveaway_id,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=None,
            name=name,
            description=description,
            prize=prize,
            winners_count=winners_count,
            requirements=requirements,
            ends_at=time.time() + (duration_hours * 3600),
            entries=[],
            winners=[],
            created_by=creator_id,
            created_at=time.time(),
            ended=False
        )
        
        self._giveaways[giveaway_id] = giveaway
        self._save_giveaway(giveaway)
        
        await self._send_giveaway_message(giveaway)
        
        return giveaway

    async def _send_giveaway_message(self, giveaway: Giveaway):
        channel = self.bot.get_channel(giveaway.channel_id)
        if not channel:
            return
        
        settings = self.get_guild_settings(giveaway.guild_id)
        emoji = settings.get("emoji", "🎉")
        
        embed = self._create_giveaway_embed(giveaway, emoji)
        view = GiveawayEntryView(self, giveaway.id)
        
        try:
            message = await channel.send(embed=embed, view=view)
            giveaway.message_id = message.id
            self._save_giveaway(giveaway)
        except Exception as e:
            logger.error(f"Failed to send giveaway message: {e}")

    def _create_giveaway_embed(self, giveaway: Giveaway, emoji: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"{emoji} {giveaway.name}",
            description=giveaway.description,
            color=discord.Color.gold()
        )
        embed.add_field(name="Prize", value=giveaway.prize, inline=False)
        embed.add_field(name="Winners", value=str(giveaway.winners_count), inline=True)
        embed.add_field(name="Ends", value=f"<t:{int(giveaway.ends_at)}:R>", inline=True)
        embed.add_field(name="Entries", value=str(len(giveaway.entries)), inline=True)
        
        if giveaway.requirements:
            req_text = self._format_requirements(giveaway.requirements)
            if req_text:
                embed.add_field(name="Requirements", value=req_text, inline=False)
        
        embed.set_footer(text=f"Giveaway ID: {giveaway.id}")
        return embed

    def _format_requirements(self, reqs: dict) -> str:
        lines = []
        if reqs.get("min_level"): lines.append(f"• Min Level: {reqs['min_level']}")
        if reqs.get("min_coins"): lines.append(f"• Min Coins: {reqs['min_coins']}")
        if reqs.get("required_roles"):
            roles = [f"<@&{rid}>" for rid in reqs["required_roles"]]
            lines.append(f"• Required Roles: {', '.join(roles)}")
        if reqs.get("forbidden_roles"):
            roles = [f"<@&{rid}>" for rid in reqs["forbidden_roles"]]
            lines.append(f"• Must NOT have: {', '.join(roles)}")
        if reqs.get("server_boost"): lines.append("• Server Boost required")
        if reqs.get("min_account_age"): lines.append(f"• Account Age: {reqs['min_account_age']}d+")
        return "\n".join(lines)

    async def enter_giveaway(self, giveaway_id: str, user_id: int, interaction: discord.Interaction) -> bool:
        if giveaway_id not in self._giveaways:
            return False
        
        giveaway = self._giveaways[giveaway_id]
        if giveaway.ended:
            await interaction.response.send_message("This giveaway has ended!", ephemeral=True)
            return False
        
        # Toggle behavior
        if user_id in giveaway.entries:
            giveaway.entries.remove(user_id)
            self._save_giveaway(giveaway)
            await self._update_giveaway_message(giveaway)
            await interaction.response.send_message("You have left the giveaway.", ephemeral=True)
            return True
        
        # Check requirements
        guild = interaction.guild
        member = guild.get_member(user_id)
        if not member: return False

        reqs = giveaway.requirements
        if reqs:
            # 1. Level
            min_level = reqs.get("min_level", 0)
            if min_level > 0:
                xp = self.bot.leveling.get_xp(guild.id, user_id)
                level = self.bot.leveling.get_level_from_xp(xp)
                if level < min_level:
                    await interaction.response.send_message(f"You need level {min_level} to enter!", ephemeral=True)
                    return False

            # 2. Coins
            min_coins = reqs.get("min_coins", 0)
            if min_coins > 0:
                coins = self.bot.economy.get_coins(guild.id, user_id)
                if coins < min_coins:
                    await interaction.response.send_message(f"You need {min_coins} coins to enter!", ephemeral=True)
                    return False
            
            # 3. Roles
            req_roles = reqs.get("required_roles", [])
            if req_roles and not any(r.id in req_roles for r in member.roles):
                await interaction.response.send_message("You don't have the required roles!", ephemeral=True)
                return False
            
            # 4. Forbidden Roles
            forb_roles = reqs.get("forbidden_roles", [])
            if any(r.id in forb_roles for r in member.roles):
                await interaction.response.send_message("You have a restricted role!", ephemeral=True)
                return False
            
            # 5. Server Boost
            if reqs.get("server_boost") and not member.premium_since:
                await interaction.response.send_message("Server Boost is required to enter!", ephemeral=True)
                return False

            # 6. Account Age
            min_age = reqs.get("min_account_age", 0)
            if min_age > 0:
                days = (discord.utils.utcnow() - member.created_at).days
                if days < min_age:
                    await interaction.response.send_message(f"Your account must be at least {min_age} days old!", ephemeral=True)
                    return False

        giveaway.entries.append(user_id)
        self._save_giveaway(giveaway)
        await self._update_giveaway_message(giveaway)
        
        entry_number = len(giveaway.entries)
        await interaction.response.send_message(f"✅ Entered! Your entry number is **#{entry_number}**", ephemeral=True)
        
        settings = self.get_guild_settings(guild.id)
        if settings.get("entry_dms"):
            try:
                await member.send(f"Confirmed entry for **{giveaway.name}** in {guild.name}! Your entry number is #{entry_number}")
            except: pass

        return True

    async def _update_giveaway_message(self, giveaway: Giveaway):
        channel = self.bot.get_channel(giveaway.channel_id)
        if not channel: return
        try:
            message = await channel.fetch_message(giveaway.message_id)
            settings = self.get_guild_settings(giveaway.guild_id)
            embed = self._create_giveaway_embed(giveaway, settings.get("emoji", "🎉"))
            await message.edit(embed=embed)
        except: pass

    async def end_giveaway(self, giveaway_id: str) -> Optional[List[int]]:
        if giveaway_id not in self._giveaways:
            return None
        
        giveaway = self._giveaways[giveaway_id]
        if giveaway.ended: return giveaway.winners
        
        giveaway.ended = True
        
        if giveaway.entries:
            # Multi-winner support with weights for bonus entries
            guild = self.bot.get_guild(giveaway.guild_id)
            settings = self.get_guild_settings(giveaway.guild_id)
            bonus_roles = settings.get("bonus_roles", {}) # {role_id_str: multiplier}

            weighted_entries = []
            for user_id in giveaway.entries:
                multiplier = 1
                if guild:
                    member = guild.get_member(user_id)
                    if member:
                        for role_id_str, mult in bonus_roles.items():
                            try:
                                if any(r.id == int(role_id_str) for r in member.roles):
                                    multiplier = max(multiplier, int(mult))
                            except: continue

                for _ in range(multiplier):
                    weighted_entries.append(user_id)
            
            # Pick unique winners
            winners = []
            unique_entries = list(set(weighted_entries))
            num_to_pick = min(giveaway.winners_count, len(unique_entries))

            potential_winners = weighted_entries.copy()
            while len(winners) < num_to_pick and potential_winners:
                winner = random.choice(potential_winners)
                if winner not in winners:
                    winners.append(winner)
                potential_winners = [u for u in potential_winners if u != winner]

            giveaway.winners = winners
        else:
            giveaway.winners = []
        
        self._save_giveaway(giveaway)
        await self._send_giveaway_results(giveaway)
        
        return giveaway.winners

    async def _send_giveaway_results(self, giveaway: Giveaway):
        channel = self.bot.get_channel(giveaway.channel_id)
        if not channel: return
        
        try:
            msg = await channel.fetch_message(giveaway.message_id)
            await msg.edit(view=None) # Remove entry buttons
        except: pass

        if giveaway.winners:
            winners_mentions = ", ".join([f"<@{w}>" for w in giveaway.winners])
            
            embed = discord.Embed(
                title=f"🎉 {giveaway.name} - Winners!",
                description=f"Congratulations to {winners_mentions}!",
                color=discord.Color.gold()
            )
            embed.add_field(name="Prize", value=giveaway.prize, inline=True)
            embed.set_footer(text=f"Giveaway ID: {giveaway.id}")

            await channel.send(content=f"Congratulations {winners_mentions}!", embed=embed)
        else:
            embed = discord.Embed(
                title=f"😢 {giveaway.name} - Ended",
                description="No entries, no winners this time.",
                color=discord.Color.red()
            )
            await channel.send(embed=embed)

    def get_active_giveaways(self, guild_id: int) -> List[Giveaway]:
        return [g for g in self._giveaways.values() 
                if g.guild_id == guild_id and not g.ended]

    async def reroll_giveaway(self, giveaway_id: str) -> Optional[List[int]]:
        gw = self._giveaways.get(giveaway_id)
        if not gw or not gw.ended or not gw.entries:
            return None

        winners = random.sample(gw.entries, min(gw.winners_count, len(gw.entries)))
        gw.winners = winners
        self._save_giveaway(gw)

        channel = self.bot.get_channel(gw.channel_id)
        if channel:
            winners_mentions = ", ".join([f"<@{w}>" for w in winners])
            embed = discord.Embed(title="🔄 Giveaway Reroll!", description=f"New winners: {winners_mentions}", color=discord.Color.gold())
            await channel.send(content=f"Reroll Results: {winners_mentions}", embed=embed)

        return winners

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        # Setup channel if not existing
        channel = discord.utils.get(guild.text_channels, name="giveaways")
        if not channel:
            channel = await guild.create_text_channel("giveaways")

        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["giveaway"] = json.dumps({"command_type": "simple", "content": "Use !giveawaypanel to manage giveaways."})
        custom_cmds["giveawaypanel"] = "configpanel giveaway"
        custom_cmds["gend"] = json.dumps({"actions": [{"name": "giveaway_end", "parameters": {"giveaway_id": "{args}"}}]})
        custom_cmds["greroll"] = json.dumps({"actions": [{"name": "giveaway_reroll", "parameters": {"giveaway_id": "{args}"}}]})
        custom_cmds["glist"] = json.dumps({"actions": [{"name": "giveaway_list", "parameters": {}}]})
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        doc_embed = discord.Embed(title="🎉 Giveaway System Guide", color=discord.Color.gold())
        doc_embed.add_field(name="Commands", value="`!giveaway` - Create/Manage giveaways\n`!gend <id>` - End a giveaway now\n`!greroll <id>` - Reroll winners\n`!glist` - List active giveaways", inline=False)
        await channel.send(embed=doc_embed)

        await interaction.followup.send(f"Giveaway system set up in {channel.mention}!", ephemeral=True)
        return True


class GiveawayEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, emoji="🎉", custom_id="gw_enter_btn")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            gw_id = interaction.message.embeds[0].footer.text.replace("Giveaway ID: ", "")
            await interaction.client.giveaways.enter_giveaway(gw_id, interaction.user.id, interaction)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(label="View Entries", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="gw_entries_btn")
    async def view_entries(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            gw_id = interaction.message.embeds[0].footer.text.replace("Giveaway ID: ", "")
            gw = interaction.client.giveaways._giveaways.get(gw_id)
            if not gw: return await interaction.response.send_message("Giveaway not found in memory.", ephemeral=True)
            status = "Entered" if interaction.user.id in gw.entries else "Not entered"
            entry_no = f" (Entry #{gw.entries.index(interaction.user.id)+1})" if interaction.user.id in gw.entries else ""
            await interaction.response.send_message(f"Total entries: {len(gw.entries)}\nYour status: {status}{entry_no}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(label="View Requirements", style=discord.ButtonStyle.secondary, emoji="🏆", custom_id="gw_reqs_btn")
    async def view_reqs(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            gw_id = interaction.message.embeds[0].footer.text.replace("Giveaway ID: ", "")
            gw = interaction.client.giveaways._giveaways.get(gw_id)
            if not gw: return await interaction.response.send_message("Giveaway not found in memory.", ephemeral=True)
            req_text = interaction.client.giveaways._format_requirements(gw.requirements) or "None"
            await interaction.response.send_message(f"**Giveaway Requirements:**\n{req_text}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
