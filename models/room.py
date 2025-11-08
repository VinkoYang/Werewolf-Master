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

# ──────────────────────────────────────────────────────────────
# 摄梦人逻辑（原 roles.py 内容）
# ──────────────────────────────────────────────────────────────
def apply_dreamer_logic(room) -> None:
    """
    夜晚结束后统一结算摄梦人技能
    1. 免疫夜间伤害（狼刀、毒）
    2. 连续两晚同一人 → 死亡
    3. 摄梦人死亡 → 梦游者同死
    """
    dreamer_user = next((u for u in room.players.values() if u.role == Role.DREAMER), None)
    if not dreamer_user:
        return

    # 1. 未手动选择 → 随机
    if dreamer_user.skill['curr_dream_target'] is None:
        alive = [u.nick for u in room.list_alive_players() if u.nick != dreamer_user.nick]
        if alive:
            dreamer_user.skill['curr_dream_target'] = random.choice(alive)

    target_nick = dreamer_user.skill['curr_dream_target']
    target_user = room.players.get(target_nick)
    if not target_user:
        return

    # 2. 连续两晚同一人 → 死亡
    if dreamer_user.skill['last_dream_target'] == target_nick:
        target_user.status = PlayerStatus.DEAD
        room.broadcast_msg(f"{target_nick} 因连续两晚被摄梦而死亡", tts=True)
        if target_user.role in (Role.HUNTER, Role.WOLF_KING):
            target_user.skill['can_shoot'] = False
        dreamer_user.skill['dreamer_nick'] = None
    else:
        # 正常梦游 → 夜间免疫
        dreamer_user.skill['dreamer_nick'] = target_nick
        target_user.skill['dream_immunity'] = True

    # 3. 保存本晚为上晚
    dreamer_user.skill['last_dream_target'] = target_nick
    dreamer_user.skill['curr_dream_target'] = None

    # 4. 摄梦人本晚已死 → 梦游者同死
    if dreamer_user.status == PlayerStatus.DEAD and dreamer_user.skill['dreamer_nick']:
        dream_nick = dreamer_user.skill['dreamer_nick']
        dream_u = room.players.get(dream_nick)
        if dream_u and dream_u.status != PlayerStatus.DEAD:
            dream_u.status = PlayerStatus.DEAD
            room.broadcast_msg(f"摄梦人死亡，梦游者 {dream_nick} 随之出局", tts=True)
            if dream_u.role in (Role.HUNTER, Role.WOLF_KING):
                dream_u.skill['can_shoot'] = False

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

    async def start_game(self):
        """房主点击【开始游戏】后执行"""
        if self.started:
            return
        # 游戏开始时，会检查玩家人数是否足够，如果不足则广播提示无法开始。
        if len(self.players) < len(self.roles):
            self.broadcast_msg("人数不足，无法开始游戏！", tts=True)
            return

        # 人数足够后，系统会洗牌发放身份，并为女巫初始化药水状态。
        self.started = True
        self.broadcast_msg("游戏开始！身份发放中...", tts=True)
        await asyncio.sleep(2)

        # 1. 洗牌发身份
        # 角色池通过随机洗牌后逐个分配给玩家，确保身份随机性
        random.shuffle(self.roles_pool)
        for user in self.players.values():
            user.role = self.roles_pool.pop()
            user.status = PlayerStatus.ALIVE
            # 女巫初始药水
            if user.role == Role.WITCH:
                user.skill['heal'] = True
                user.skill['poison'] = True
            # 其他神职默认 can_shoot = True（已在 __post_init__）
            user.send_msg(f"你的身份是：{user.role.value}")
            #self.broadcast_msg(f"{user.nick} 身份已发放", tts=False)

        await asyncio.sleep(3)

        # 2. 启动夜晚主线程（只启动一次）
        # 身份发放后，启动游戏主循环线程，并设置等待状态为False。
        if self.logic_thread is None:
            self.logic_thread = run_async(self.game_loop())

        # 3. 房主不再看到“开始游戏”按钮
        self.waiting = False

    async def game_loop(self):
        """游戏主循环：夜晚 → 天亮 → 检查胜负"""
        while True:
            if not self.started:
                await asyncio.sleep(1)
                continue

            # 夜晚
            await self.night_logic()

            # 这里白天阶段已经由 night_logic 最后设置成 GameStage.Day
            # 房主会看到“公布死亡”按钮，点完后会再次进入 night_logic

            # 检查胜负（防止卡死）
            await self.check_game_end()

            await asyncio.sleep(1)

    async def check_game_end(self):
        """每轮结束后检查是否结束"""
        alive_wolves = [u for u in self.list_alive_players() if u.role in (Role.WOLF, Role.WOLF_KING)]
        alive_gods = [u for u in self.list_alive_players() if u.role in (Role.SEER, Role.WITCH, Role.GUARD, Role.HUNTER, Role.DREAMER)]
        alive_citizens = [u for u in self.list_alive_players() if u.role == Role.CITIZEN]

        good_alive = len(alive_gods) + len(alive_citizens)
        wolf_alive = len(alive_wolves)

        if wolf_alive == 0:
            await self.end_game("好人阵营获胜！狼人全部出局")
            return
        if wolf_alive >= good_alive:
            await self.end_game("狼人阵营获胜！好人被屠光")
            return

    async def end_game(self, reason: str):
        """游戏结束"""
        self.started = False
        self.stage = None
        self.broadcast_msg(f"游戏结束，{reason}。", tts=True)
        await asyncio.sleep(2)

        for nick, user in self.players.items():
            role_name = user.role.value if user.role else "无"
            self.broadcast_msg(f"{nick}：{role_name}", tts=True)
            user.role = None
            user.status = None
            user.skill.clear()

        # 清理线程
        if self.logic_thread:
            self.logic_thread.close()
            self.logic_thread = None

    async def night_logic(self):
        """单夜逻辑"""
        logger.info(f"=== 第 {self.round + 1} 夜 开始 ===")
        self.round += 1
        self.broadcast_msg('天黑请闭眼', tts=True)
        await asyncio.sleep(3)

        # 狼人
        self.stage = GameStage.WOLF
        self.broadcast_msg('狼人请出现', tts=True)
        await asyncio.sleep(1)
        self.waiting = True
        await self.wait_for_player()
        await asyncio.sleep(1)

        # 狼人未刀人 → 随机补刀
        if not any(u.status == PlayerStatus.PENDING_DEAD for u in self.players.values()):
            alive = self.list_alive_players()
            if alive:
                target = random.choice(alive)
                target.status = PlayerStatus.PENDING_DEAD
                self.broadcast_msg(f"狼人未操作，系统随机刀了 {target.nick}", tts=True)

        self.broadcast_msg('狼人请闭眼', tts=True)
        await asyncio.sleep(2)

        # 预言家
        if Role.SEER in self.roles:
            self.stage = GameStage.SEER
            self.broadcast_msg('预言家请出现', tts=True)
            await asyncio.sleep(1)
            self.waiting = True
            await self.wait_for_player()
            await asyncio.sleep(1)
            self.broadcast_msg('预言家请闭眼', tts=True)
            await asyncio.sleep(2)

        # 女巫
        if Role.WITCH in self.roles:
            self.stage = GameStage.WITCH
            self.broadcast_msg('女巫请出现', tts=True)
            await asyncio.sleep(1)
            self.waiting = True
            await self.wait_for_player()
            await asyncio.sleep(1)
            self.broadcast_msg('女巫请闭眼', tts=True)
            await asyncio.sleep(2)

        # 守卫
        if Role.GUARD in self.roles:
            self.stage = GameStage.GUARD
            self.broadcast_msg('守卫请出现', tts=True)
            await asyncio.sleep(1)
            self.waiting = True
            await self.wait_for_player()
            await asyncio.sleep(1)
            self.broadcast_msg('守卫请闭眼', tts=True)
            await asyncio.sleep(2)

        # 摄梦人
        if Role.DREAMER in self.roles:
            self.stage = GameStage.DREAMER
            self.broadcast_msg('摄梦人请出现', tts=True)
            await asyncio.sleep(1)
            self.waiting = True
            await self.wait_for_player()
            await asyncio.sleep(1)
            self.broadcast_msg('摄梦人请闭眼', tts=True)
            await asyncio.sleep(2)

        # 猎人（仅查看开枪状态）
        if Role.HUNTER in self.roles:
            self.stage = GameStage.HUNTER
            self.broadcast_msg('猎人请出现', tts=True)
            await asyncio.sleep(1)
            self.waiting = True
            await self.wait_for_player()
            await asyncio.sleep(1)
            self.broadcast_msg('猎人请闭眼', tts=True)
            await asyncio.sleep(2)

        # 结算摄梦人逻辑
        apply_dreamer_logic(self)

        # 结算夜晚死亡
        dead_this_night = []
        for u in self.players.values():
            if u.status == PlayerStatus.DEAD:
                continue
            immunity = u.skill.get('dream_immunity', False)
            u.skill['dream_immunity'] = False  # 重置免疫

            if u.status == PlayerStatus.PENDING_POISON:
                if immunity:
                    u.status = PlayerStatus.ALIVE
                else:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)
                    if u.role in (Role.HUNTER, Role.WOLF_KING):
                        u.skill['can_shoot'] = False
            elif u.status == PlayerStatus.PENDING_DEAD:
                if immunity or u.status == PlayerStatus.PENDING_HEAL or u.status == PlayerStatus.PENDING_GUARD:
                    u.status = PlayerStatus.ALIVE
                else:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)
                    # 假设被刀杀的猎人/狼王可以开枪，不设置 can_shoot = False
            elif u.status in (PlayerStatus.PENDING_HEAL, PlayerStatus.PENDING_GUARD):
                u.status = PlayerStatus.ALIVE
            else:
                u.status = PlayerStatus.ALIVE

        self.death_pending = dead_this_night

        # 天亮
        self.broadcast_msg('天亮请睁眼', tts=True)
        await asyncio.sleep(2)

        # 设置白天阶段（房主可以看到公布死亡按钮）
        self.stage = GameStage.Day

        # 如果是第一天，切换到 SHERIFF
        if self.round == 1:
            self.stage = GameStage.SHERIFF
            self.broadcast_msg('进行警上竞选', tts=True)

    async def wait_for_player(self):
        """等待玩家行动完成"""
        timeout = 60  # 示例超时时间
        start_time = asyncio.get_event_loop().time()
        while self.waiting:
            if asyncio.get_event_loop().time() - start_time > timeout:
                self.waiting = False
                self.broadcast_msg("行动超时，系统自动跳过", tts=True)
                break
            await asyncio.sleep(0.1)

    def list_alive_players(self) -> List[User]:
        return [u for u in self.players.values() if u.status == PlayerStatus.ALIVE]

    def is_full(self) -> bool:
        return len(self.players) >= len(self.roles)

    def is_no_god(self) -> bool:
        """判断是否配置了神职"""
        god_roles = [Role.SEER, Role.WITCH, Role.GUARD, Role.HUNTER, Role.DREAMER]
        return not any(god in self.roles for god in god_roles)

    def add_player(self, user: 'User'):
        """添加玩家"""
        if user.room or user.nick in self.players:
            raise AssertionError
        self.players[user.nick] = user
        user.room = self
        user.start_syncer()

        status = f'人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}'
        user.game_msg.append(status)
        self.broadcast_msg(status)
        logger.info(f'用户 "{user.nick}" 加入房间 "{self.id}"')

    def remove_player(self, user: 'User'):
        """移除玩家"""
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
        """获取房主"""
        return next(iter(self.players.values())) if self.players else None

    def send_msg(self, text: str, nick: str):
        """发送私聊消息"""
        self.log.append((nick, text))

    def broadcast_msg(self, text: str, tts=False):
        """广播消息"""
        if tts:
            say(text)
        self.log.append((Config.SYS_NICK, text))

    def broadcast_log_ctrl(self, ctrl_type: LogCtrl):
        """发送控制指令"""
        self.log.append((None, ctrl_type))

    def desc(self):
        """房间描述"""
        return f'房间号 {self.id}，需要玩家 {len(self.roles)} 人，人员配置：{dict(Counter(self.roles))}'

    async def vote_kill(self, nick: str):
        player = self.players.get(nick)
        if player:
            player.status = PlayerStatus.DEAD
            self.broadcast_msg(f"{nick} 被投票出局", tts=True)
            # Handle hunter/wolf king shoot if applicable
            
    @classmethod
    def get(cls, room_id) -> Optional['Room']:
        """获取房间"""
        return Global.get_room(room_id)

    @classmethod
    def validate_room_join(cls, room_id):
        """验证加入房间"""
        room = cls.get(room_id)
        if not room:
            return '房间不存在'
        if room.is_full():
            return '房间已满'

    @classmethod
    def alloc(cls, room_setting) -> 'Room':
        """创建房间"""
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
