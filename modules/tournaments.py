import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from data_manager import dm
from logger import logger


class TournamentStatus(Enum):
    SETUP = "setup"
    REGISTRATION = "registration"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TournamentType(Enum):
    SINGLE_ELIMINATION = "single_elimination"
    DOUBLE_ELIMINATION = "double_elimination"
    ROUND_ROBIN = "round_robin"
    FREE_FOR_ALL = "free_for_all"
    TEAM_VS_TEAM = "team_vs_team"


@dataclass
class Tournament:
    id: str
    guild_id: int
    name: str
    description: str
    tournament_type: TournamentType
    status: TournamentStatus
    max_participants: int
    min_participants: int
    prize_pool: dict
    registration_end: float
    start_time: float
    rounds: List[dict]
    participants: List[int]
    teams: Dict[str, List[int]]
    bracket: List[dict]
    winner: Optional[int]
    created_by: int
    created_at: float
    channel_id: Optional[int]


@dataclass
class Match:
    id: str
    tournament_id: str
    round: int
    match_number: int
    player1: Optional[int]
    player2: Optional[int]
    player1_score: int
    player2_score: int
    winner: Optional[int]
    status: str
    next_match: Optional[str]


class TournamentSystem:
    def __init__(self, bot):
        self.bot = bot
        self._tournaments: Dict[str, Tournament] = {}
        self._active_matches: Dict[str, Match] = {}
        self._load_tournaments()

    def _load_tournaments(self):
        data = dm.load_json("tournaments", default={})
        
        for tourney_id, t_data in data.items():
            try:
                tournament = Tournament(
                    id=tourney_id,
                    guild_id=t_data["guild_id"],
                    name=t_data["name"],
                    description=t_data["description"],
                    tournament_type=TournamentType(t_data["tournament_type"]),
                    status=TournamentStatus(t_data["status"]),
                    max_participants=t_data["max_participants"],
                    min_participants=t_data["min_participants"],
                    prize_pool=t_data["prize_pool"],
                    registration_end=t_data["registration_end"],
                    start_time=t_data["start_time"],
                    rounds=t_data.get("rounds", []),
                    participants=t_data.get("participants", []),
                    teams=t_data.get("teams", {}),
                    bracket=t_data.get("bracket", []),
                    winner=t_data.get("winner"),
                    created_by=t_data["created_by"],
                    created_at=t_data["created_at"],
                    channel_id=t_data.get("channel_id")
                )
                self._tournaments[tourney_id] = tournament
            except Exception as e:
                logger.error(f"Failed to load tournament {tourney_id}: {e}")

    def _save_tournament(self, tournament: Tournament):
        data = dm.load_json("tournaments", default={})
        data[tournament.id] = {
            "guild_id": tournament.guild_id,
            "name": tournament.name,
            "description": tournament.description,
            "tournament_type": tournament.tournament_type.value,
            "status": tournament.status.value,
            "max_participants": tournament.max_participants,
            "min_participants": tournament.min_participants,
            "prize_pool": tournament.prize_pool,
            "registration_end": tournament.registration_end,
            "start_time": tournament.start_time,
            "rounds": tournament.rounds,
            "participants": tournament.participants,
            "teams": tournament.teams,
            "bracket": tournament.bracket,
            "winner": tournament.winner,
            "created_by": tournament.created_by,
            "created_at": tournament.created_at,
            "channel_id": tournament.channel_id
        }
        dm.save_json("tournaments", data)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "tournament_settings", {
            "enabled": True,
            "default_max": 32,
            "default_min": 4,
            "default_prize": {"coins": 500, "xp": 250}
        })

    async def create_tournament(self, guild_id: int, creator_id: int, name: str,
                               tournament_type: TournamentType, max_participants: int,
                               prize_pool: dict, channel_id: int) -> Tournament:
        tournament_id = f"tournament_{guild_id}_{int(time.time())}"
        
        tournament = Tournament(
            id=tournament_id,
            guild_id=guild_id,
            name=name,
            description=f"Competitive {tournament_type.value} tournament",
            tournament_type=tournament_type,
            status=TournamentStatus.REGISTRATION,
            max_participants=max_participants,
            min_participants=4,
            prize_pool=prize_pool,
            registration_end=time.time() + 86400,
            start_time=time.time() + 90000,
            rounds=[],
            participants=[],
            teams={},
            bracket=[],
            winner=None,
            created_by=creator_id,
            created_at=time.time(),
            channel_id=channel_id
        )
        
        self._tournaments[tournament_id] = tournament
        self._save_tournament(tournament)
        
        return tournament

    async def generate_bracket(self, tournament: Tournament):
        import random
        
        participants = tournament.participants.copy()
        random.shuffle(participants)
        
        if tournament.tournament_type == TournamentType.SINGLE_ELIMINATION:
            bracket_size = 2
            while bracket_size < len(participants):
                bracket_size *= 2
            
            while len(participants) < bracket_size:
                participants.append(None)
            
            bracket = []
            num_rounds = bracket_size.bit_length()
            
            for round_num in range(num_rounds):
                round_matches = []
                matches_in_round = bracket_size // (2 ** (round_num + 1))
                
                for match_num in range(matches_in_round):
                    match = {
                        "round": round_num,
                        "match_number": match_num,
                        "player1": participants[match_num * 2] if match_num * 2 < len(participants) else None,
                        "player2": participants[match_num * 2 + 1] if match_num * 2 + 1 < len(participants) else None,
                        "player1_score": 0,
                        "player2_score": 0,
                        "winner": None,
                        "status": "pending"
                    }
                    round_matches.append(match)
                
                bracket.append(round_matches)
            
            tournament.bracket = bracket
            self._save_tournament(tournament)

    async def register_participant(self, tournament_id: str, user_id: int) -> bool:
        if tournament_id not in self._tournaments:
            return False
        
        tournament = self._tournaments[tournament_id]
        
        if tournament.status != TournamentStatus.REGISTRATION:
            return False
        
        if len(tournament.participants) >= tournament.max_participants:
            return False
        
        if user_id in tournament.participants:
            return False
        
        tournament.participants.append(user_id)
        self._save_tournament(tournament)
        
        return True

    async def unregister_participant(self, tournament_id: str, user_id: int) -> bool:
        if tournament_id not in self._tournaments:
            return False
        
        tournament = self._tournaments[tournament_id]
        
        if user_id not in tournament.participants:
            return False
        
        tournament.participants.remove(user_id)
        self._save_tournament(tournament)
        
        return True

    async def start_tournament(self, tournament_id: str) -> bool:
        if tournament_id not in self._tournaments:
            return False
        
        tournament = self._tournaments[tournament_id]
        
        if len(tournament.participants) < tournament.min_participants:
            return False
        
        tournament.status = TournamentStatus.ACTIVE
        await self.generate_bracket(tournament)
        
        self._save_tournament(tournament)
        
        return True

    async def record_match_result(self, tournament_id: str, round_num: int,
                                  match_num: int, winner_id: int, score: tuple) -> bool:
        if tournament_id not in self._tournaments:
            return False
        
        tournament = self._tournaments[tournament_id]
        
        if round_num >= len(tournament.bracket):
            return False
        
        round_matches = tournament.bracket[round_num]
        if match_num >= len(round_matches):
            return False
        
        match = round_matches[match_num]
        match["winner"] = winner_id
        match["player1_score"] = score[0]
        match["player2_score"] = score[1]
        match["status"] = "completed"
        
        if round_num < len(tournament.bracket) - 1:
            next_round_matches = tournament.bracket[round_num + 1]
            next_match_num = match_num // 2
            next_match = next_round_matches[next_match_num]
            
            if match_num % 2 == 0:
                next_match["player1"] = winner_id
            else:
                next_match["player2"] = winner_id
        
        self._save_tournament(tournament)
        
        await self._check_tournament_complete(tournament)
        
        return True

    async def _check_tournament_complete(self, tournament: Tournament):
        final_round = tournament.bracket[-1] if tournament.bracket else []
        final_match = final_round[0] if final_round else None
        
        if final_match and final_match["winner"]:
            tournament.status = TournamentStatus.COMPLETED
            tournament.winner = final_match["winner"]
            
            await self._distribute_prizes(tournament)
            
            self._save_tournament(tournament)

    async def _distribute_prizes(self, tournament: Tournament):
        if not tournament.prize_pool:
            return
        
        prize_xp = tournament.prize_pool.get("xp", 0)
        prize_coins = tournament.prize_pool.get("coins", 0)
        
        if tournament.winner:
            user_data = dm.get_guild_data(tournament.guild_id, f"user_{tournament.winner}", {})
            user_data["xp"] = user_data.get("xp", 0) + prize_xp
            user_data["coins"] = user_data.get("coins", 0) + prize_coins
            user_data["tournaments_won"] = user_data.get("tournaments_won", 0) + 1
            dm.update_guild_data(tournament.guild_id, f"user_{tournament.winner}", user_data)
        
        if tournament.bracket:
            second_place = None
            if len(tournament.bracket[-1]) > 1:
                for match in tournament.bracket[-1]:
                    if match["winner"] != tournament.winner and match["winner"]:
                        second_place = match["winner"]
                        break
            
            if second_place:
                second_prize_xp = int(prize_xp * 0.5)
                second_prize_coins = int(prize_coins * 0.5)
                
                user_data = dm.get_guild_data(tournament.guild_id, f"user_{second_place}", {})
                user_data["xp"] = user_data.get("xp", 0) + second_prize_xp
                user_data["coins"] = user_data.get("coins", 0) + second_prize_coins
                dm.update_guild_data(tournament.guild_id, f"user_{second_place}", user_data)

    async def create_team_tournament(self, guild_id: int, creator_id: int, name: str,
                                    team_size: int, max_teams: int, prize_pool: dict,
                                    channel_id: int) -> Tournament:
        tournament_id = f"tournament_{guild_id}_{int(time.time())}"
        
        tournament = Tournament(
            id=tournament_id,
            guild_id=guild_id,
            name=name,
            description=f"Team vs Team tournament (size: {team_size})",
            tournament_type=TournamentType.TEAM_VS_TEAM,
            status=TournamentStatus.REGISTRATION,
            max_participants=max_teams,
            min_participants=2,
            prize_pool=prize_pool,
            registration_end=time.time() + 86400,
            start_time=time.time() + 90000,
            rounds=[],
            participants=[],
            teams={},
            bracket=[],
            winner=None,
            created_by=creator_id,
            created_at=time.time(),
            channel_id=channel_id
        )
        
        self._tournaments[tournament_id] = tournament
        self._save_tournament(tournament)
        
        return tournament

    def get_tournament_leaderboard(self, guild_id: int) -> List[dict]:
        all_users = {}
        
        data = dm.load_json("tournaments", default={})
        
        for tourney_id, t_data in data.items():
            if t_data["guild_id"] != guild_id:
                continue
            
            winner = t_data.get("winner")
            if winner:
                if winner not in all_users:
                    all_users[winner] = {"wins": 0, "participated": 0}
                all_users[winner]["wins"] += 1
            
            for participant in t_data.get("participants", []):
                if participant not in all_users:
                    all_users[participant] = {"wins": 0, "participated": 0}
                all_users[participant]["participated"] += 1
        
        sorted_users = sorted(all_users.items(), key=lambda x: x[1]["wins"], reverse=True)[:10]
        
        leaderboard = []
        for i, (user_id, stats) in enumerate(sorted_users):
            leaderboard.append({
                "rank": i + 1,
                "user_id": user_id,
                "wins": stats["wins"],
                "participated": stats["participated"]
            })
        
        return leaderboard

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "tournament_settings", settings)
        
        help_embed = discord.Embed(
            title="🏆 Tournament System",
            description="Create competitive tournaments with brackets, prizes, and seasons.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Create tournaments with auto-generated brackets. Prize pool comes from economy. Supports single elimination, round robin, and team vs team.",
            inline=False
        )
        help_embed.add_field(
            name="!tournaments",
            value="List active tournaments.",
            inline=False
        )
        help_embed.add_field(
            name="!join <tournament>",
            value="Join a tournament.",
            inline=False
        )
        help_embed.add_field(
            name="!tournamentleaderboard",
            value="View tournament winners leaderboard.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["tournaments"] = json.dumps({
            "command_type": "list_tournaments"
        })
        custom_cmds["join"] = json.dumps({
            "command_type": "join_tournament"
        })
        custom_cmds["tournamentleaderboard"] = json.dumps({
            "command_type": "tournament_leaderboard"
        })
        custom_cmds["help tournaments"] = json.dumps({
            "command_type": "help_embed",
            "title": "🏆 Tournament System",
            "description": "Create competitive tournaments.",
            "fields": [
                {"name": "!tournaments", "value": "List active tournaments.", "inline": False},
                {"name": "!join <tournament>", "value": "Join a tournament.", "inline": False},
                {"name": "!tournamentleaderboard", "value": "View leaderboard.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
