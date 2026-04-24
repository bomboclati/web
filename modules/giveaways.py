import discord
from discord import ui
import asyncio
import json
import time
import random
from typing import Dict, List, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from data_manager import dm
from logger import logger
import os

@dataclass
class Giveaway:
    id: str
    guild_id: int
    channel_id: int
    message_id: Optional[int]
    prize: str
    winners_count: int
    ends_at: float
    requirements: dict = field(default_factory=dict)
    entries: List[int] = field(default_factory=list)
    winners: List[int] = field(default_factory=list)
    created_by: int = 0
    created_at: float = field(default_factory=time.time)
    ended: bool = False
    name: str = "" # Prize usually serves as name
    description: str = ""

class GiveawayPersistentView(ui.View):
    """Persistent view for giveaway entry buttons."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Enter Giveaway", emoji="🎉", style=discord.ButtonStyle.success, custom_id="gw_enter")
    async def enter(self, interaction: discord.Interaction, button: ui.Button):
        await self._handle_entry(interaction)

    @ui.button(label="View Entries", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="gw_view")
    async def view_entries(self, interaction: discord.Interaction, button: ui.Button):
        gw_system = interaction.client.giveaways
        gw = await gw_system.get_giveaway_from_message(interaction.message.id, interaction.guild.id)
        if not gw:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)

        unique_entries = list(set(gw.entries))
        entry_status = "Entered ✅" if interaction.user.id in gw.entries else "Not Entered ❌"

        entry_num = "N/A"
        if interaction.user.id in gw.entries:
            # Find the first index + 1
            entry_num = f"#{unique_entries.index(interaction.user.id) + 1}"

        await interaction.response.send_message(
            f"**Giveaway Info:**\n"
            f"· Total Unique Entries: {len(unique_entries)}\n"
            f"· Your Status: {entry_status}\n"
            f"· Your Entry Number: {entry_num}",
            ephemeral=True
        )

    @ui.button(label="View Requirements", emoji="🏆", style=discord.ButtonStyle.secondary, custom_id="gw_reqs")
    async def view_reqs(self, interaction: discord.Interaction, button: ui.Button):
        gw_system = interaction.client.giveaways
        gw = await gw_system.get_giveaway_from_message(interaction.message.id, interaction.guild.id)
        if not gw:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)

        reqs = gw.requirements
        if not reqs:
            return await interaction.response.send_message("✅ No requirements for this giveaway!", ephemeral=True)

        lines = []
        if reqs.get("min_level"): lines.append(f"· Minimum Level: {reqs['min_level']}")
        if reqs.get("min_coins"): lines.append(f"· Minimum Coins: {reqs['min_coins']}")
        if reqs.get("required_roles"):
            mentions = [f"<@&{r}>" for r in reqs["required_roles"]]
            lines.append(f"· Required Roles: {', '.join(mentions)}")
        if reqs.get("blacklisted_roles"):
            mentions = [f"<@&{r}>" for r in reqs["blacklisted_roles"]]
            lines.append(f"· Must NOT have: {', '.join(mentions)}")
        if reqs.get("boost_required"): lines.append("· Server Boost required")
        if reqs.get("min_account_age_days"): lines.append(f"· Minimum Account Age: {reqs['min_account_age_days']} days")

        await interaction.response.send_message(
            f"**Giveaway Requirements:**\n" + "\n".join(lines),
            ephemeral=True
        )

    async def _handle_entry(self, interaction: discord.Interaction):
        gw_system = interaction.client.giveaways
        gw = await gw_system.get_giveaway_from_message(interaction.message.id, interaction.guild.id)

        if not gw or gw.ended:
            return await interaction.response.send_message("❌ This giveaway has already ended.", ephemeral=True)

        user_id = interaction.user.id

        # Toggle behavior
        if user_id in gw.entries:
            # Leave
            gw.entries = [uid for uid in gw.entries if uid != user_id]
            await gw_system._save_giveaway(gw)
            await gw_system._update_giveaway_embed(gw)
            return await interaction.response.send_message("❌ You have left the giveaway.", ephemeral=True)

        # Check requirements
        met, reason = await gw_system.check_requirements(interaction.user, gw)
        if not met:
            return await interaction.response.send_message(f"❌ You don't meet the requirements: {reason}", ephemeral=True)

        # Handle entry with bonus
        multiplier = await gw_system.get_multiplier(interaction.user)
        for _ in range(multiplier):
            gw.entries.append(user_id)

        await gw_system._save_giveaway(gw)
        await gw_system._update_giveaway_embed(gw)

        unique_entries = list(set(gw.entries))
        entry_num = unique_entries.index(user_id) + 1

        await interaction.response.send_message(
            f"✅ You've entered the giveaway for **{gw.prize}**!\n"
            f"Your entry number is **#{entry_num}**" + (f" (Bonus x{multiplier} applied!)" if multiplier > 1 else ""),
            ephemeral=True
        )

        # Entry DM
        settings = gw_system.get_guild_settings(interaction.guild.id)
        if settings.get("entry_dms", True):
            try:
                await interaction.user.send(
                    f"🎉 **Entry Confirmed!**\n"
                    f"You have entered the giveaway for **{gw.prize}** in **{interaction.guild.name}**.\n"
                    f"Your entry number: **#{entry_num}**"
                )
            except:
                pass

class GiveawaySystem:
    def __init__(self, bot):
        self.bot = bot
        self._giveaways: Dict[str, Giveaway] = {}
        self._load_giveaways()

    def _load_giveaways(self):
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
                                prize=gw_data.get("prize", gw_data.get("name", "Unknown Prize")),
                                winners_count=gw_data["winners_count"],
                                requirements=gw_data.get("requirements", {}),
                                ends_at=gw_data["ends_at"],
                                entries=gw_data.get("entries", []),
                                winners=gw_data.get("winners", []),
                                created_by=gw_data.get("created_by", 0),
                                created_at=gw_data.get("created_at", time.time()),
                                ended=gw_data.get("ended", False)
                            )
                            if not giveaway.ended:
                                self._giveaways[gw_id] = giveaway
                                count += 1
                    except Exception as e:
                        logger.error(f"Failed to load giveaways from {filename}: {e}")
        logger.info(f"Loaded {count} active giveaways.")

    async def _save_giveaway(self, giveaway: Giveaway):
        guild_id = giveaway.guild_id
        giveaways = dm.get_guild_data(guild_id, "giveaways", {})
        giveaways[giveaway.id] = {
            "channel_id": giveaway.channel_id,
            "message_id": giveaway.message_id,
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
        if not giveaway.ended:
            self._giveaways[giveaway.id] = giveaway
        elif giveaway.id in self._giveaways:
            del self._giveaways[giveaway.id]

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "giveaway_settings", {
            "enabled": True,
            "default_channel": None,
            "entry_dms": True,
            "bonus_roles": {} # role_id: multiplier
        })

    async def get_giveaway_from_message(self, message_id: int, guild_id: int) -> Optional[Giveaway]:
        for gw in self._giveaways.values():
            if gw.message_id == message_id and gw.guild_id == guild_id:
                return gw
        # Try loading from disk if not in active cache
        giveaways = dm.get_guild_data(guild_id, "giveaways", {})
        for gw_id, data in giveaways.items():
            if data.get("message_id") == message_id:
                return Giveaway(id=gw_id, guild_id=guild_id, **data)
        return None

    async def check_requirements(self, member: discord.Member, giveaway: Giveaway) -> (bool, str):
        reqs = giveaway.requirements
        if not reqs: return True, ""

        # Min level
        min_level = reqs.get("min_level")
        if min_level:
            user_level = dm.get_guild_data(member.guild.id, f"user_{member.id}", {}).get("level", 1)
            if user_level < min_level:
                return False, f"Requires Level {min_level} (You: {user_level})"

        # Min coins
        min_coins = reqs.get("min_coins")
        if min_coins:
            user_coins = dm.get_guild_data(member.guild.id, f"user_{member.id}", {}).get("coins", 0)
            if user_coins < min_coins:
                return False, f"Requires {min_coins} coins (You: {user_coins})"

        # Required roles
        req_roles = reqs.get("required_roles")
        if req_roles:
            member_role_ids = [r.id for r in member.roles]
            if not any(rid in member_role_ids for rid in req_roles):
                return False, "Missing one or more required roles."

        # Blacklisted roles
        black_roles = reqs.get("blacklisted_roles")
        if black_roles:
            member_role_ids = [r.id for r in member.roles]
            if any(rid in member_role_ids for rid in black_roles):
                return False, "You have a role that is blacklisted from this giveaway."

        # Boost required
        if reqs.get("boost_required") and not member.premium_since:
            return False, "Server boosting is required."

        # Account age
        min_age = reqs.get("min_account_age_days")
        if min_age:
            age = (datetime.now(timezone.utc) - member.created_at).days
            if age < min_age:
                return False, f"Account must be at least {min_age} days old (Yours: {age} days)"

        return True, ""

    async def get_multiplier(self, member: discord.Member) -> int:
        settings = self.get_guild_settings(member.guild.id)
        bonus_roles = settings.get("bonus_roles", {})
        if not bonus_roles: return 1

        max_mult = 1
        member_role_ids = [str(r.id) for r in member.roles]
        for rid, mult in bonus_roles.items():
            if str(rid) in member_role_ids:
                max_mult = max(max_mult, mult)
        return max_mult

    async def create_giveaway(self, guild_id: int, channel_id: int, creator_id: int,
                           prize: str, winners_count: int, duration_seconds: int,
                           requirements: dict = None) -> Giveaway:
        gw_id = f"gw_{int(time.time())}_{random.randint(100, 999)}"
        ends_at = time.time() + duration_seconds
        
        giveaway = Giveaway(
            id=gw_id,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=None,
            prize=prize,
            winners_count=winners_count,
            ends_at=ends_at,
            requirements=requirements or {},
            entries=[],
            winners=[],
            created_by=creator_id,
            created_at=time.time(),
            ended=False
        )
        
        # Send initial embed
        channel = self.bot.get_channel(channel_id)
        if channel:
            embed = self._create_giveaway_embed(giveaway)
            view = GiveawayPersistentView()
            msg = await channel.send(embed=embed, view=view)
            giveaway.message_id = msg.id

        await self._save_giveaway(giveaway)
        return giveaway

    def _create_giveaway_embed(self, gw: Giveaway) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎉 GIVEAWAY: {gw.prize}",
            description=f"Click the button below to enter!",
            color=discord.Color.blue() if not gw.ended else discord.Color.greyple()
        )
        
        unique_entries = len(set(gw.entries))
        embed.add_field(name="🎁 Prize", value=gw.prize, inline=True)
        embed.add_field(name="👥 Winners", value=str(gw.winners_count), inline=True)
        embed.add_field(name="📊 Entries", value=str(unique_entries), inline=True)
        
        if not gw.ended:
            embed.add_field(name="🕒 Ends", value=f"<t:{int(gw.ends_at)}:R>", inline=True)
        else:
            embed.add_field(name="🕒 Status", value="Ended", inline=True)
            if gw.winners:
                mentions = [f"<@{uid}>" for uid in gw.winners]
                embed.add_field(name="🏆 Winners", value=", ".join(mentions), inline=False)
            else:
                embed.add_field(name="🏆 Winners", value="No entries/winners", inline=False)

        req_text = "None"
        if gw.requirements:
            lines = []
            r = gw.requirements
            if r.get("min_level"): lines.append(f"Level {r['min_level']}+")
            if r.get("min_coins"): lines.append(f"{r['min_coins']} coins+")
            if r.get("boost_required"): lines.append("Boosting")
            if r.get("required_roles"): lines.append(f"{len(r['required_roles'])} Roles")
            if lines: req_text = ", ".join(lines)

        embed.add_field(name="🏆 Requirements", value=req_text, inline=True)
        embed.set_footer(text=f"ID: {gw.id}")
        return embed

    async def _update_giveaway_embed(self, gw: Giveaway):
        channel = self.bot.get_channel(gw.channel_id)
        if not channel: return
        
        try:
            msg = await channel.fetch_message(gw.message_id)
            await msg.edit(embed=self._create_giveaway_embed(gw))
        except:
            pass

    async def end_giveaway(self, gw_id: str):
        gw = self._giveaways.get(gw_id)
        if not gw or gw.ended: return
        
        gw.ended = True
        
        if gw.entries:
            # unique entries to avoid picking same person multiple times if they have bonus
            # BUT they should have higher chance.
            # Weighted random selection
            weights = {}
            for uid in gw.entries:
                weights[uid] = weights.get(uid, 0) + 1
            
            candidates = list(weights.keys())
            probs = [weights[uid] for uid in candidates]
            
            # Pick unique winners
            winners = []
            num_to_pick = min(gw.winners_count, len(candidates))
            
            for _ in range(num_to_pick):
                winner = random.choices(candidates, weights=probs, k=1)[0]
                winners.append(winner)
                # Remove winner from candidates for next pick
                idx = candidates.index(winner)
                candidates.pop(idx)
                probs.pop(idx)

            gw.winners = winners
        
        await self._save_giveaway(gw)
        
        # Update original message
        channel = self.bot.get_channel(gw.channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(gw.message_id)
                await msg.edit(embed=self._create_giveaway_embed(gw), view=None)

                if gw.winners:
                    mentions = ", ".join([f"<@{uid}>" for uid in gw.winners])
                    announce = await channel.send(f"🎊 Congratulations {mentions}! You won **{gw.prize}**!")
                    try: await announce.pin() # Optional per blueprint? Usually good.
                    except: pass
                else:
                    await channel.send(f"😢 The giveaway for **{gw.prize}** has ended, but no one entered.")
            except:
                pass

    async def reroll_giveaway(self, gw_id: str, guild_id: int) -> Optional[List[int]]:
        # Load from disk as it's likely ended
        giveaways = dm.get_guild_data(guild_id, "giveaways", {})
        data = giveaways.get(gw_id)
        if not data or not data.get("ended"): return None
        
        gw = Giveaway(id=gw_id, guild_id=guild_id, **data)
        
        # Exclude previous winners
        remaining = [uid for uid in gw.entries if uid not in gw.winners]
        if not remaining: return None
        
        # Weighted pick for one new winner
        weights = {}
        for uid in remaining:
            weights[uid] = weights.get(uid, 0) + 1
        
        candidates = list(weights.keys())
        probs = [weights[uid] for uid in candidates]
        
        new_winner = random.choices(candidates, weights=probs, k=1)[0]
        gw.winners.append(new_winner)
        
        await self._save_giveaway(gw)
        
        channel = self.bot.get_channel(gw.channel_id)
        if channel:
            await channel.send(f"🔄 **Reroll:** Congratulations <@{new_winner}>! You are the new winner of **{gw.prize}**!")
            await self._update_giveaway_embed(gw)
            
        return [new_winner]

    def start_giveaway_monitor(self):
        asyncio.create_task(self._giveaway_monitor_loop())

    async def _giveaway_monitor_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                now = time.time()
                # Use a copy of keys to avoid modification error
                active_ids = list(self._giveaways.keys())
                for gw_id in active_ids:
                    gw = self._giveaways.get(gw_id)
                    if gw and not gw.ended and gw.ends_at <= now:
                        await self.end_giveaway(gw_id)
            except Exception as e:
                logger.error(f"Giveaway monitor error: {e}")
            await asyncio.sleep(30)

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "giveaway_settings", settings)
        
        # Create #giveaways
        giveaways_channel = discord.utils.get(guild.text_channels, name="giveaways")
        if not giveaways_channel:
            giveaways_channel = await guild.create_text_channel("giveaways", topic="Server Giveaways and Prizes")
        
        # Register commands (logic in ActionHandler)
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        cmds = ["giveaway", "gend", "greroll", "glist"]
        for c in cmds:
            custom_cmds[c] = json.dumps({"command_type": f"gw_{c}"})
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        # Doc
        embed = discord.Embed(title="🎉 Giveaway System Setup Complete", color=discord.Color.green())
        embed.add_field(name="Commands", value="`!giveaway` - Create\n`!gend` - End\n`!greroll` - Reroll\n`!glist` - List active", inline=False)
        embed.add_field(name="Channel", value=giveaways_channel.mention, inline=False)
        await interaction.followup.send(embed=embed)
        return True
