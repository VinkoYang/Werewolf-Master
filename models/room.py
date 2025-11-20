# models/room.py
import asyncio
import random
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Union, Any

from pywebio.session import run_async
from pywebio.session.coroutinebased import TaskHandler

from enums import Role, WitchRule, GuardRule, GameStage, LogCtrl, PlayerStatus
from models.system import Global, Config
from models.user import User
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

role_classes = {
    Role.CITIZEN: Citizen,
    Role.WOLF: Wolf,
    Role.WOLF_KING: WolfKing,
    Role.SEER: Seer,
    Role.WITCH: Witch,
    Role.GUARD: Guard,
    Role.HUNTER: Hunter,
    Role.DREAMER: Dreamer,
}

@dataclass
class Room:
    id: Optional[int] = None
    roles: List[Role] = field(default_factory=list)
    witch_rule: WitchRule = WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY
    guard_rule: GuardRule = GuardRule.MED_CONFLICT

    started: bool = False
    roles_pool: List[Role] = field(default_factory=list)
    players: Dict[str, User] = field(default_factory=dict)
    round: int = 0
    stage: Optional[GameStage] = None
    waiting: bool = False
    log: List[Tuple[Union[str, None], Union[str, LogCtrl]]] = field(default_factory=list)
    skill: Dict[str, Any] = field(default_factory=dict)  # 用于狼人击杀等

    logic_thread: Optional[TaskHandler] = None
    game_over: bool = False

    death_pending: List[str] = field(default_factory=list)
    current_speaker: Optional[str] = None
    sheriff_speakers: Optional[List[str]] = None
    sheriff_speaker_index: int = 0

    async def start_game(self):
        if self.started or len(self.players) < len(self.roles):
            return
        self.started = True
        self.game_over = False
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

        for idx, user in enumerate(self.players.values(), 1):
            user.seat = idx

        seat_msg = "座位表： " + " | ".join(f"{u.seat}号: {u.nick}" for u in sorted(self.players.values(), key=lambda x: x.seat))
        self.broadcast_msg(seat_msg, tts=False)
        await asyncio.sleep(3)

        if not self.logic_thread:
            self.logic_thread = run_async(self.game_loop())
        self.waiting = False

    async def game_loop(self):
        while not self.game_over:
            if not self.started:
                await asyncio.sleep(1); continue
            await self.night_logic()
            await self.check_game_end()
            await asyncio.sleep(1)
        self.logic_thread = None

    async def night_logic(self):
        logger.info(f"=== 第 {self.round + 1} 夜 开始 ===")
        self.round += 1
        # 在天黑提示前加上夜数隔断，便于在 Public 区分每晚开始
        self.broadcast_msg(f"============ 第 {self.round} 晚 ============")
        self.broadcast_msg('天黑请闭眼', tts=True)
        await asyncio.sleep(3)

        # ---------- 狼人 ----------
        self.stage = GameStage.WOLF
        for user in self.players.values():
            user.skill['acted_this_stage'] = False
        self.broadcast_msg('狼人请出现', tts=True)
        await asyncio.sleep(1)
        self.waiting = True
        await self.wait_for_player()

        # 统一结算狼人击杀（统计票数，最多票者为今晚被刀）
        wolf_votes = self.skill.get('wolf_votes', {})
        if wolf_votes:
            # 计算每个目标的票数
            counts = {t: len(voters) for t, voters in wolf_votes.items()}
            max_count = max(counts.values())
            candidates = [t for t, c in counts.items() if c == max_count]
            chosen = random.choice(candidates) if len(candidates) > 1 else candidates[0]
            target = self.players.get(chosen)
            if target and target.status == PlayerStatus.ALIVE:
                target.status = PlayerStatus.PENDING_DEAD
            # 将被狼选择的信息仅发送给狼人私聊（非公开）
            for u in self.players.values():
                if u.role in (Role.WOLF, Role.WOLF_KING):
                    try:
                        # 显式调用 room.send_msg，确保消息被标记为私聊（recipient = u.nick）
                        self.send_msg(f"狼人选择了 {chosen}", nick=u.nick)
                    except Exception:
                        pass
            # 清理投票记录
            if 'wolf_votes' in self.skill:
                del self.skill['wolf_votes']
            # 清理玩家临时选择
            for u in self.players.values():
                u.skill.pop('wolf_choice', None)
        else:
            # 狼人空刀也应为狼人私聊信息
            for u in self.players.values():
                if u.role in (Role.WOLF, Role.WOLF_KING):
                    try:
                        self.send_msg("狼人空刀", nick=u.nick)
                    except Exception:
                        pass

        await asyncio.sleep(1)
        self.broadcast_msg('狼人请闭眼', tts=True)
        await asyncio.sleep(2)

        # ---------- 其他神职 ----------
        night_roles = [
            (GameStage.SEER, Role.SEER),
            (GameStage.WITCH, Role.WITCH),
            (GameStage.GUARD, Role.GUARD),
            (GameStage.DREAMER, Role.DREAMER),
        ]

        for stage, role_enum in night_roles:
            if any(u.role == role_enum and u.status == PlayerStatus.ALIVE for u in self.players.values()):
                self.stage = stage
                for user in self.players.values():
                    user.skill['acted_this_stage'] = False
                self.broadcast_msg(f'{stage.value}请出现', tts=True)
                await asyncio.sleep(1)
                self.waiting = True
                await self.wait_for_player()
                await asyncio.sleep(1)
                self.broadcast_msg(f'{stage.value}请闭眼', tts=True)
                await asyncio.sleep(2)

        # ---------- 摄梦人结算 ----------
        dreamer = next((u for u in self.players.values() if u.role == Role.DREAMER and u.status == PlayerStatus.ALIVE), None)
        if dreamer:
            dreamer.role_instance.apply_logic(self)

        # ---------- 夜晚死亡结算 ----------
        dead_this_night = []
        for u in self.players.values():
            if u.status == PlayerStatus.DEAD:
                continue
            immunity = u.skill.get('dream_immunity', False)
            u.skill['dream_immunity'] = False

            if u.status == PlayerStatus.PENDING_POISON:
                if not immunity:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)
                    if u.role in (Role.HUNTER, Role.WOLF_KING):
                        u.skill['can_shoot'] = False
                else:
                    u.status = PlayerStatus.ALIVE

            elif u.status == PlayerStatus.PENDING_DEAD:
                if immunity or u.status in (PlayerStatus.PENDING_HEAL, PlayerStatus.PENDING_GUARD):
                    u.status = PlayerStatus.ALIVE
                else:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)

            elif u.status in (PlayerStatus.PENDING_HEAL, PlayerStatus.PENDING_GUARD):
                u.status = PlayerStatus.ALIVE
            else:
                u.status = PlayerStatus.ALIVE

        self.death_pending = dead_this_night
        self.broadcast_msg('天亮请睁眼', tts=True)
        await asyncio.sleep(2)
        self.stage = GameStage.Day
        if self.round == 1:
            self.stage = GameStage.SHERIFF
            self.broadcast_msg('进行警上竞选', tts=True)

    async def wait_for_player(self):
        timeout = 20
        start = asyncio.get_event_loop().time()
        while self.waiting:
            if asyncio.get_event_loop().time() - start > timeout:
                self.waiting = False
                self.broadcast_msg("行动超时，系统自动跳过", tts=True)
                break
            await asyncio.sleep(0.1)

    async def check_game_end(self):
        wolves = [u for u in self.list_alive_players() if u.role in (Role.WOLF, Role.WOLF_KING)]
        goods = [u for u in self.list_alive_players() if u.role not in (Role.WOLF, Role.WOLF_KING)]
        if not wolves:
            await self.end_game("好人阵营获胜！狼人全部出局")
        elif len(wolves) >= len(goods):
            await self.end_game("狼人阵营获胜！好人被屠光")

    async def end_game(self, reason: str):
        if self.game_over: return
        self.game_over = True
        self.started = False
        self.stage = None
        self.broadcast_msg(f"游戏结束，{reason}。", tts=True)
        await asyncio.sleep(2)
        for nick, user in self.players.items():
            self.broadcast_msg(f"{nick}：{user.role_instance.name if user.role_instance else '无'}", tts=True)
            user.role = user.role_instance = user.status = None
            user.skill.clear()
        logger.info(f"房间 {self.id} 游戏结束：{reason}")

    def list_alive_players(self) -> List[User]:
        return [u for u in self.players.values() if u.status == PlayerStatus.ALIVE]

    def list_pending_kill_players(self) -> List[User]:
        """返回本夜被标记为待死亡（被狼人击中）的玩家列表"""
        return [u for u in self.players.values() if u.status == PlayerStatus.PENDING_DEAD]

    def is_full(self) -> bool:
        return len(self.players) >= len(self.roles)

    def add_player(self, user: 'User'):
        if user.room or user.nick in self.players: raise AssertionError
        self.players[user.nick] = user
        user.room = self
        user.start_syncer()
        user.seat = len(self.players)
        status = f'人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}'
        user.game_msg.append(status)
        self.broadcast_msg(status)
        logger.info(f'用户 "{user.nick}" 加入房间 "{self.id}"，座位 {user.seat}')

    def remove_player(self, user: 'User'):
        if user.nick not in self.players: raise AssertionError
        self.players.pop(user.nick)
        user.stop_syncer()
        user.room = None
        if not self.players:
            Global.remove_room(self.id)
            return
        self.broadcast_msg(f'人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}')
        logger.info(f'用户 "{user.nick}" 离开房间 "{self.id}"')

    def get_host(self) -> User:
        return next(iter(self.players.values())) if self.players else None

    def send_msg(self, text: str, nick: str):
        self.log.append((nick, text))

    def broadcast_msg(self, text: str, tts: bool = False):
        if tts: say(text)
        self.log.append((Config.SYS_NICK, text))

    def desc(self) -> str:
        return f'房间号 {self.id}，需要玩家 {len(self.roles)} 人，人员配置：{dict(Counter(self.roles))}'

    async def vote_kill(self, nick: str):
        player = self.players.get(nick)
        if not player: return
        player.status = PlayerStatus.DEAD
        self.broadcast_msg(f"{nick} 被投票出局", tts=True)
        if player.role in (Role.HUNTER, Role.WOLF_KING) and player.skill.get('can_shoot', False):
            player.send_msg('你被投票出局，立即开枪！')

    @classmethod
    def get(cls, room_id) -> Optional['Room']:
        return Global.get_room(room_id)

    @classmethod
    def validate_room_join(cls, room_id):
        room = cls.get(room_id)
        if not room: return '房间不存在'
        if room.is_full(): return '房间已满'

    @classmethod
    def alloc(cls, room_setting) -> 'Room':
        roles = []
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
                current_speaker=None,
                sheriff_speakers=None,
                sheriff_speaker_index=0,
            )
        )
