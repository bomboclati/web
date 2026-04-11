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
        data = dm.load_json("giveaways", default={})
        
        for gw_id, gw_data in data.items():
            try:
                giveaway = Giveaway(
                    id=gw_id,
                    guild_id=gw_data["guild_id"],
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
                
                if not giveaway.ended and giveaway.ends_at > time.time():
                    self._giveaways[gw_id] = giveaway
            except Exception as e:
                logger.error(f"Failed to load giveaway {gw_id}: {e}")

    def _save_giveaway(self, giveaway: Giveaway):
        data = dm.load_json("giveaways", default={})
        data[giveaway.id] = {
            "guild_id": giveaway.guild_id,
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
        dm.save_json("giveaways", data)

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
            "emoji": "🎁"
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
        emoji = settings.get("emoji", "🎁")
        
        ends_at = datetime.fromtimestamp(giveaway.ends_at, tz=datetime.timezone.utc)
        time_left = ends_at - discord.utils.utcnow()
        
        embed = discord.Embed(
            title=f"{emoji} {giveaway.name}",
            description=giveaway.description,
            color=discord.Color.gold()
        )
        embed.add_field(name="Prize", value=giveaway.prize, inline=False)
        embed.add_field(name="Winners", value=str(giveaway.winners_count), inline=True)
        
        time_str = f"{time_left.days}d {time_left.seconds // 3600}h"
        embed.add_field(name="Ends", value=f"<t:{int(giveaway.ends_at)}:R>", inline=True)
        embed.add_field(name="Entries", value=str(len(giveaway.entries)), inline=True)
        
        if giveaway.requirements:
            req_text = []
            if giveaway.requirements.get("required_roles"):
                req_text.append("Required roles")
            if giveaway.requirements.get("min_xp"):
                req_text.append(f"Min XP: {giveaway.requirements['min_xp']}")
            if giveaway.requirements.get("min_messages"):
                req_text.append(f"Min messages: {giveaway.requirements['min_messages']}")
            if req_text:
                embed.add_field(name="Requirements", value=", ".join(req_text), inline=False)
        
        view = discord.ui.View()
        enter_btn = discord.ui.Button(label="Enter", style=discord.ButtonStyle.primary, custom_id=f"giveaway_enter_{giveaway.id}")
        
        async def enter_callback(interaction: discord.Interaction):
            await self.enter_giveaway(giveaway.id, interaction.user.id, interaction)
        
        enter_btn.callback = enter_callback
        view.add_item(enter_btn)
        
        try:
            message = await channel.send(embed=embed, view=view)
            giveaway.message_id = message.id
            self._save_giveaway(giveaway)
        except Exception as e:
            logger.error(f"Failed to send giveaway message: {e}")

    async def enter_giveaway(self, giveaway_id: str, user_id: int, interaction: discord.Interaction = None) -> bool:
        if giveaway_id not in self._giveaways:
            return False
        
        giveaway = self._giveaways[giveaway_id]
        
        if giveaway.ended:
            return False
        
        if user_id in giveaway.entries:
            if interaction:
                await interaction.response.send_message("You already entered!", ephemeral=True)
            return False
        
        if giveaway.requirements:
            user_data = dm.get_guild_data(giveaway.guild_id, f"user_{user_id}", {})
            
            min_xp = giveaway.requirements.get("min_xp", 0)
            if user_data.get("xp", 0) < min_xp:
                if interaction:
                    await interaction.response.send_message(f"You need {min_xp} XP to enter!", ephemeral=True)
                return False
            
            min_messages = giveaway.requirements.get("min_messages", 0)
            if user_data.get("total_messages", 0) < min_messages:
                if interaction:
                    await interaction.response.send_message(f"You need {min_messages} messages to enter!", ephemeral=True)
                return False
            
            required_roles = giveaway.requirements.get("required_roles", [])
            if required_roles:
                member = self.bot.get_guild(giveaway.guild_id).get_member(user_id)
                if member:
                    user_role_ids = [r.id for r in member.roles]
                    if not any(int(r) in user_role_ids for r in required_roles):
                        if interaction:
                            await interaction.response.send_message("You don't have the required roles!", ephemeral=True)
                        return False
        
        giveaway.entries.append(user_id)
        self._save_giveaway(giveaway)
        
        if interaction:
            await interaction.response.send_message(f"Entered! Total entries: {len(giveaway.entries)}", ephemeral=True)
        
        return True

    async def end_giveaway(self, giveaway_id: str) -> Optional[List[int]]:
        if giveaway_id not in self._giveaways:
            return None
        
        giveaway = self._giveaways[giveaway_id]
        
        giveaway.ended = True
        
        if giveaway.entries:
            winners = random.sample(giveaway.entries, min(giveaway.winners_count, len(giveaway.entries)))
            giveaway.winners = winners
            
            for winner_id in winners:
                user_data = dm.get_guild_data(giveaway.guild_id, f"user_{winner_id}", {})
                reward_coins = giveaway.requirements.get("reward_coins", 100)
                reward_xp = giveaway.requirements.get("reward_xp", 50)
                user_data["coins"] = user_data.get("coins", 0) + reward_coins
                user_data["xp"] = user_data.get("xp", 0) + reward_xp
                dm.update_guild_data(giveaway.guild_id, f"user_{winner_id}", user_data)
        else:
            giveaway.winners = []
        
        self._save_giveaway(giveaway)
        
        await self._send_giveaway_results(giveaway)
        
        return giveaway.winners

    async def _send_giveaway_results(self, giveaway: Giveaway):
        channel = self.bot.get_channel(giveaway.channel_id)
        if not channel:
            return
        
        settings = self.get_guild_settings(giveaway.guild_id)
        emoji = settings.get("emoji", "🎁")
        
        if giveaway.winners:
            winners_mentions = ", ".join([f"<@{w}>" for w in giveaway.winners])
            
            embed = discord.Embed(
                title=f"🎉 {giveaway.name} - Winners!",
                description=f"Congratulations to the winners!",
                color=discord.Color.gold()
            )
            embed.add_field(name="Winners", value=winners_mentions, inline=False)
            embed.add_field(name="Prize", value=giveaway.prize, inline=True)
        else:
            embed = discord.Embed(
                title=f"😢 {giveaway.name} - Ended",
                description="No entries, no winners this time.",
                color=discord.Color.red()
            )
        
        try:
            await channel.send(embed=embed)
        except:
            pass

    def get_active_giveaways(self, guild_id: int) -> List[Giveaway]:
        return [g for g in self._giveaways.values() 
                if g.guild_id == guild_id and not g.ended]

    def get_user_entries(self, guild_id: int, user_id: int) -> List[str]:
        return [g.id for g in self._giveaways.values() 
                if g.guild_id == guild_id and user_id in g.entries and not g.ended]

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "giveaway_settings", settings)
        
        try:
            doc_channel = await guild.create_text_channel("giveaways-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        doc_embed = discord.Embed(title="🎁 Giveaway System Guide", description="Complete guide to hosting giveaways!", color=discord.Color.gold())
        doc_embed.add_field(name="📖 How It Works", value="Host giveaways with requirements. Users enter by clicking button. Winners randomly selected when time ends.", inline=False)
        doc_embed.add_field(name="🎮 Available Commands", value="**!giveaway** - List active giveaways\n**!enter <name>** - Enter a giveaway\n**!help giveaway** - Show this guide", inline=False)
        doc_embed.add_field(name="💡 How to Host", value="Use `/bot Create a giveaway` to have AI set one up with your prize, requirements, and duration!", inline=False)
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** Active giveaways will appear with an Enter button!")
        
        help_embed = discord.Embed(title="🎁 Giveaway System", description="Host giveaways with requirements and multiple winners.", color=discord.Color.green())
        help_embed.add_field(name="How it works", value="Create giveaways with role/XP/message requirements. Multiple winners supported. Auto-selects winners when time ends.", inline=False)
        help_embed.add_field(name="!giveaway", value="List active giveaways.", inline=False)
        help_embed.add_field(name="!enter <giveaway>", value="Enter a giveaway.", inline=False)
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["giveaway"] = json.dumps({
            "command_type": "list_giveaways"
        })
        custom_cmds["enter"] = json.dumps({
            "command_type": "enter_giveaway"
        })
        custom_cmds["help giveaway"] = json.dumps({
            "command_type": "help_embed",
            "title": "🎁 Giveaway System",
            "description": "Host giveaways.",
            "fields": [
                {"name": "!giveaway", "value": "List active giveaways.", "inline": False},
                {"name": "!enter <name>", "value": "Enter a giveaway.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
