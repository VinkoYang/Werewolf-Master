# models/room.py
import asyncio
import random
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Union, Any

from pywebio.session import run_async
from pywebio.session.coroutinebased import TaskHandler

from enums import Role, WitchRule, GuardRule, SheriffBombRule, GameStage, LogCtrl, PlayerStatus
from presets.base import BaseGameConfig
from presets.game_config_registry import resolve_game_config_class
from models.system import Global, Config
from models.user import User
from models.room_runtime import RoomRuntimeMixin
from utils import say
from . import logger

# ---------- 角色类 ----------
from roles.citizen import Citizen
from roles.wolf import Wolf
from roles.wolf_king import WolfKing
from roles.seer import Seer
from roles.witch import Witch
from roles.guard import Guard
from roles.hunter import Hunter
from roles.dreamer import Dreamer
from roles.idiot import Idiot
from roles.half_blood import HalfBlood
from roles.white_wolf_king import WhiteWolfKing
from roles.nine_tailed_fox import NineTailedFox

role_classes = {
    Role.CITIZEN: Citizen,
    Role.WOLF: Wolf,
    Role.WOLF_KING: WolfKing,
    Role.WHITE_WOLF_KING: WhiteWolfKing,
    Role.SEER: Seer,
    Role.WITCH: Witch,
    Role.GUARD: Guard,
    Role.HUNTER: Hunter,
    Role.DREAMER: Dreamer,
    Role.IDIOT: Idiot,
    Role.HALF_BLOOD: HalfBlood,
    Role.NINE_TAILED_FOX: NineTailedFox,
}


@dataclass
class Room(RoomRuntimeMixin):
    id: Optional[int] = None
    roles: List[Role] = field(default_factory=list)
    witch_rule: WitchRule = WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY
    guard_rule: GuardRule = GuardRule.MED_CONFLICT
    sheriff_bomb_rule: SheriffBombRule = SheriffBombRule.DOUBLE_LOSS

    started: bool = False
    roles_pool: List[Role] = field(default_factory=list)
    players: Dict[str, User] = field(default_factory=dict)
    round: int = 0
    stage: Optional[GameStage] = None
    waiting: bool = False
    log: List[Tuple[Union[str, None], Union[str, LogCtrl]]] = field(default_factory=list)
    skill: Dict[str, Any] = field(default_factory=dict)

    logic_thread: Optional[TaskHandler] = None
    game_over: bool = False
    _game_config: Optional[BaseGameConfig] = field(default=None, init=False, repr=False)

    death_pending: List[str] = field(default_factory=list)
    current_speaker: Optional[str] = None
    sheriff_speakers: Optional[List[str]] = None
    sheriff_speaker_index: int = 0
    sheriff_state: Dict[str, Any] = field(default_factory=dict)
    day_state: Dict[str, Any] = field(default_factory=dict)
    sheriff_badge_destroyed: bool = False
    seat_state_version: int = 0

    async def start_game(self):
        if self.started or len(self.players) < len(self.roles):
            return
        standing_players = [u for u in self.players.values() if not u.seat]
        if standing_players:
            names = '、'.join(u.nick for u in standing_players)
            self.broadcast_msg(f'无法开始游戏：以下玩家尚未就座 → {names}')
            return
        self.started = True
        self.game_over = False
        self._ensure_game_config()
        # 在游戏开始时添加公共隔断，提升可读性
        self.broadcast_msg('=' * 22)
        self.broadcast_msg("游戏开始！身份发放中...", tts=True)
        await asyncio.sleep(2)

        random.shuffle(self.roles_pool)
        for user in self.players.values():
            role_enum = self.roles_pool.pop()
            user.role = role_enum
            user.role_instance = role_classes[role_enum](user)
            user.status = PlayerStatus.ALIVE
            if user.role == Role.WITCH:
                user.skill['heal'] = user.skill['poison'] = True
            if user.role in (Role.HUNTER, Role.WOLF_KING):
                user.skill['can_shoot'] = True
            user.send_msg(f"你的身份是：{user.role_instance.name}")

        seat_adjusted = False
        for user in self.players.values():
            if not user.seat or user.seat < 1:
                user.seat = self._pick_available_seat(None)
                seat_adjusted = True
            user.send_msg(f'你当前的号码牌：{user.seat}号')
        if seat_adjusted:
            self._mark_seat_state_dirty()
        await asyncio.sleep(3)

        if not self.logic_thread:
            self.logic_thread = run_async(self.game_loop())
        self.waiting = False

    def is_full(self) -> bool:
        return len(self.players) >= len(self.roles)

    def add_player(self, user: 'User'):
        if user.room or user.nick in self.players:
            raise AssertionError
        seat = self._pick_available_seat(user.seat)
        self.players[user.nick] = user
        user.room = self
        user.start_syncer()
        user.seat = seat
        status = f'【{user.nick}】进入房间，人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}'
        user.game_msg.append(status)
        self.broadcast_msg(status)
        logger.info(f'用户 "{user.nick}" 加入房间 "{self.id}"，座位 {user.seat}')
        user.send_msg(f'你当前的号码牌：{user.seat}号')
        self._mark_seat_state_dirty()

    def remove_player(self, user: 'User'):
        if user.nick not in self.players:
            raise AssertionError
        self.players.pop(user.nick)
        user.stop_syncer()
        user.room = None
        user.seat = None
        if not self.players:
            Global.remove_room(self.id)
            return
        self.broadcast_msg(f'人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}')
        self._mark_seat_state_dirty()
        logger.info(f'用户 "{user.nick}" 离开房间 "{self.id}"')

    def get_host(self) -> User:
        return next(iter(self.players.values())) if self.players else None

    def send_msg(self, text: str, nick: str):
        self.log.append((nick, text))

    def broadcast_msg(self, text: str, tts: bool = False):
        if tts:
            say(text)
        self.log.append((Config.SYS_NICK, text))

    def desc(self) -> str:
        return f'房间号 {self.id}，需要玩家 {len(self.roles)} 人，人员配置：{dict(Counter(self.roles))}'

    @classmethod
    def get(cls, room_id) -> Optional['Room']:
        return Global.get_room(room_id)

    @classmethod
    def validate_room_join(cls, room_id):
        room = cls.get(room_id)
        if not room:
            return '房间不存在'
        if room.is_full():
            return '房间已满'

    def _mark_seat_state_dirty(self):
        self.seat_state_version += 1

    def get_seat_snapshot(self):
        total = len(self.roles)
        seat_map = {u.seat: u.nick for u in self.players.values() if u.seat}
        seats = []
        for seat in range(1, total + 1):
            seats.append({'seat': seat, 'nick': seat_map.get(seat)})
        standing = [u.nick for u in self.players.values() if not u.seat]
        return {
            'version': self.seat_state_version,
            'seats': seats,
            'standing': standing
        }

    def _pick_available_seat(self, preferred: Optional[int]) -> int:
        max_players = len(self.roles)
        if max_players <= 0:
            raise ValueError('房间尚未配置人数')
        taken = {u.seat for u in self.players.values() if u.seat}
        if preferred is not None:
            if preferred < 1 or preferred > max_players:
                raise ValueError('座位号不存在')
            if preferred in taken:
                raise ValueError('座位已被占用')
            return preferred
        for seat in range(1, max_players + 1):
            if seat not in taken:
                return seat
        raise ValueError('没有可用座位')

    def list_available_seats(self) -> List[int]:
        max_players = len(self.roles)
        taken = {u.seat for u in self.players.values() if u.seat}
        return [seat for seat in range(1, max_players + 1) if seat not in taken]

    def release_seat(self, user: 'User'):
        if not user.seat:
            return
        logger.info(f'玩家 {user.nick} 起立，释放座位 {user.seat}')
        user.seat = None
        self._mark_seat_state_dirty()

    def assign_seat(self, user: 'User', seat: Optional[int] = None):
        seat_num = self._pick_available_seat(seat)
        user.seat = seat_num
        logger.info(f'玩家 {user.nick} 坐在 {seat_num} 号')
        self._mark_seat_state_dirty()

    @classmethod
    def alloc(cls, room_setting) -> 'Room':
        roles: List[Role] = []
        roles.extend([Role.WOLF] * room_setting['wolf_num'])
        roles.extend([Role.CITIZEN] * room_setting['citizen_num'])
        roles.extend(Role.from_option(room_setting['god_wolf']))
        roles.extend(Role.from_option(room_setting['god_citizen']))

        return Global.reg_room(
            cls(
                id=None,
                roles=copy(roles),
                witch_rule=WitchRule.from_option(room_setting['witch_rule']),
                guard_rule=GuardRule.from_option(room_setting['guard_rule']),
                sheriff_bomb_rule=SheriffBombRule.from_option(
                    room_setting.get('sheriff_bomb_rule', SheriffBombRule.DOUBLE_LOSS.value)
                ),
                started=False,
                roles_pool=copy(roles),
                players=dict(),
                round=0,
                stage=None,
                waiting=False,
                log=list(),
                skill=dict(),
                logic_thread=None,
                game_over=False,
                death_pending=[],
                day_state=dict(),
                current_speaker=None,
                sheriff_speakers=None,
                sheriff_speaker_index=0,
            )
        )
