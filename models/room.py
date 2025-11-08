# models/room.py
import asyncio
import random
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Union

from pywebio import run_async
from pywebio.session.coroutinebased import TaskHandle

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

# 注册表（新增角色只需在这里加一行）
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
    # Static settings
    roles: List[Role] = field(default_factory=list)
    witch_rule: WitchRule = WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY
    guard_rule: GuardRule = GuardRule.MED_CONFLICT

    # Dynamic
    started: bool = False
    roles_pool: List[Role] = field(default_factory=list)
    players: Dict[str, User] = field(default_factory=dict)
    round: int = 0
    stage: Optional[GameStage] = None
    waiting: bool = False
    log: List[Tuple[Union[str, None], Union[str, LogCtrl]]] = field(default_factory=list)

    # Internal
    logic_thread: Optional[TaskHandle] = None

    # 额外属性
    death_pending: List[str] = field(default_factory=list)
    current_speaker: Optional[str] = None
    sheriff_speakers: Optional[List[str]] = None
    sheriff_speaker_index: int = 0

    # ------------------------------------------------------------------
    # 游戏启动
    # ------------------------------------------------------------------
    async def start_game(self):
        if self.started:
            return
        if len(self.players) < len(self.roles):
            self.broadcast_msg("人数不足，无法开始游戏！", tts=True)
            return

        self.started = True
        self.broadcast_msg("游戏开始！身份发放中...", tts=True)
        await asyncio.sleep(2)

        random.shuffle(self.roles_pool)
        for user in self.players.values():
            role_enum = self.roles_pool.pop()
            user.role = role_enum
            user.role_instance = role_classes[role_enum](user)   # 实例化
            user.status = PlayerStatus.ALIVE

            # 女巫初始药水（其它角色可在自己的类里自行初始化）
            if user.role == Role.WITCH:
                user.skill['heal'] = True
                user.skill['poison'] = True

            # 使用角色类里定义的中文名
            user.send_msg(f"你的身份是：{user.role_instance.name}")


        # 分配座位号（加入顺序）
        for idx, user in enumerate(self.players.values(), start=1):
            user.seat = idx

        # 广播座位表（关键：这里才能用 self）
        seat_msg = "座位表： " + " | ".join(f"{u.seat}号: {u.nick}" for u in sorted(self.players.values(), key=lambda x: x.seat))
        self.broadcast_msg(seat_msg, tts=True)
        
        await asyncio.sleep(3)

        if self.logic_thread is None:
            self.logic_thread = run_async(self.game_loop())
        self.waiting = False
        


    # ------------------------------------------------------------------
    # 游戏主循环
    # ------------------------------------------------------------------
    async def game_loop(self):
        while True:
            if not self.started:
                await asyncio.sleep(1)
                continue
            await self.night_logic()
            await self.check_game_end()
            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # 夜晚逻辑（统一遍历 can_act_at_night 的角色）
    # ------------------------------------------------------------------
    async def night_logic(self):
        logger.info(f"=== 第 {self.round + 1} 夜 开始 ===")
        self.round += 1
        self.broadcast_msg('天黑请闭眼', tts=True)
        await asyncio.sleep(3)

        # ---------- 狼人 ----------
        self.stage = GameStage.WOLF
        self.broadcast_msg('狼人请出现', tts=True)
        await asyncio.sleep(1)
        self.waiting = True
        await self.wait_for_player()
        await asyncio.sleep(1)

        # 若狼人全部未操作，系统随机刀
        if not any(u.status == PlayerStatus.PENDING_DEAD for u in self.players.values()):
            wolves = [u for u in self.players.values()
                      if u.role in (Role.WOLF, Role.WOLF_KING) and u.status == PlayerStatus.ALIVE]
            if wolves:
                target = random.choice(self.list_alive_players())
                target.status = PlayerStatus.PENDING_DEAD
                self.broadcast_msg(f"狼人未操作，系统随机刀了 {target.nick}", tts=True)

        self.broadcast_msg('狼人请闭眼', tts=True)
        await asyncio.sleep(2)

        # ---------- 其它夜晚阶段 ----------
        night_roles = [
            (GameStage.SEER,   Role.SEER),
            (GameStage.WITCH,  Role.WITCH),
            (GameStage.GUARD,  Role.GUARD),
            (GameStage.DREAMER,Role.DREAMER),
            (GameStage.HUNTER, Role.HUNTER),
        ]

        for stage, role_enum in night_roles:
            # 只要该角色存在且 can_act_at_night 为 True，就进入阶段
            if any(u.role == role_enum and u.role_instance.can_act_at_night
                   for u in self.players.values()):
                self.stage = stage
                self.broadcast_msg(f'{stage.value}请出现', tts=True)
                await asyncio.sleep(1)
                self.waiting = True
                await self.wait_for_player()
                await asyncio.sleep(1)
                self.broadcast_msg(f'{stage.value}请闭眼', tts=True)
                await asyncio.sleep(2)

        # ---------- 摄梦人结算 ----------
        dreamer = next((u for u in self.players.values() if u.role == Role.DREAMER), None)
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
                if immunity:
                    u.status = PlayerStatus.ALIVE
                else:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)
                    if u.role in (Role.HUNTER, Role.WOLF_KING):
                        u.skill['can_shoot'] = False

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

    # ------------------------------------------------------------------
    # 等待玩家行动（超时自动跳过）
    # ------------------------------------------------------------------
    async def wait_for_player(self):
        timeout = 20 # 20倒计时
        start = asyncio.get_event_loop().time()
        while self.waiting:
            if asyncio.get_event_loop().time() - start > timeout:
                self.waiting = False
                self.broadcast_msg("行动超时，系统自动跳过", tts=True)
                break
            await asyncio.sleep(0.1)

    # ------------------------------------------------------------------
    # 胜负判定
    # ------------------------------------------------------------------
    async def check_game_end(self):
        alive_wolves = [u for u in self.list_alive_players()
                        if u.role in (Role.WOLF, Role.WOLF_KING)]
        alive_goods = [u for u in self.list_alive_players()
                       if u.role not in (Role.WOLF, Role.WOLF_KING)]

        if not alive_wolves:
            await self.end_game("好人阵营获胜！狼人全部出局")
            return
        if len(alive_wolves) >= len(alive_goods):
            await self.end_game("狼人阵营获胜！好人被屠光")
            return

    async def end_game(self, reason: str):
        self.started = False
        self.stage = None
        self.broadcast_msg(f"游戏结束，{reason}。", tts=True)
        await asyncio.sleep(2)

        for nick, user in self.players.items():
            role_name = user.role_instance.name if user.role_instance else "无"
            self.broadcast_msg(f"{nick}：{role_name}", tts=True)
            user.role = None
            user.role_instance = None
            user.status = None
            user.skill.clear()

        if self.logic_thread:
            self.logic_thread.close()
            self.logic_thread = None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def list_alive_players(self) -> List[User]:
        return [u for u in self.players.values() if u.status == PlayerStatus.ALIVE]

    def list_pending_kill_players(self) -> List[User]:
        """女巫阶段显示被刀的玩家"""
        return [u for u in self.players.values() if u.status == PlayerStatus.PENDING_DEAD]

    def is_full(self) -> bool:
        return len(self.players) >= len(self.roles)

    def add_player(self, user: 'User'):
        if user.room or user.nick in self.players:
            raise AssertionError
        self.players[user.nick] = user
        user.room = self
        user.start_syncer()

        # 分配座位号：从 1 开始
        user.seat = len(self.players)  # 加入顺序即座位号

        status = f'人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}'
        user.game_msg.append(status)
        self.broadcast_msg(status)
        logger.info(f'用户 "{user.nick}" 加入房间 "{self.id}"，座位 {user.seat}')

    def remove_player(self, user: 'User'):
        if user.nick not in self.players:
            raise AssertionError
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
        if tts:
            say(text)
        self.log.append((Config.SYS_NICK, text))

    def broadcast_log_ctrl(self, ctrl_type: LogCtrl):
        self.log.append((None, ctrl_type))

    def desc(self) -> str:
        return f'房间号 {self.id}，需要玩家 {len(self.roles)} 人，人员配置：{dict(Counter(self.roles))}'

    async def vote_kill(self, nick: str):
        player = self.players.get(nick)
        if not player:
            return
        player.status = PlayerStatus.DEAD
        self.broadcast_msg(f"{nick} 被投票出局", tts=True)

        # 猎人/狼王开枪判定（skill 中保存）
        if player.role in (Role.HUNTER, Role.WOLF_KING) and player.skill.get('can_shoot', False):
            player.send_msg('你被投票出局，立即开枪！')
            # 这里可以再加开枪 UI（略）

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
                logic_thread=None,
                death_pending=[],
                current_speaker=None,
                sheriff_speakers=None,
                sheriff_speaker_index=0,
            )
        )
