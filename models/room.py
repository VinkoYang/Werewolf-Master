# models/room.py
import asyncio
import random
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Union

from pywebio.session.coroutinebased import TaskHandle

from enums import Role, WitchRule, GuardRule, GameStage, LogCtrl, PlayerStatus
from models.system import Global, Config
from models.user import User
from utils import say
from . import logger


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
            await asyncio.sleep(1.5)

            hunter = next((u for u in self.players.values() if u.role == Role.HUNTER), None)
            if hunter:
                can_shoot = hunter.skill.get('can_shoot', True)
                status = "可以开枪" if can_shoot else "无法开枪（被毒杀或梦游死亡）"
                # 直接发私聊，绕过 player_action 等待机制
                hunter.send_msg(f'Private: 你的开枪状态：{status}')

            await asyncio.sleep(3)
            self.broadcast_msg('猎人请闭眼', tts=True)
            await asyncio.sleep(2)

        # === 摄梦人技能统一结算 ===
        try:
            from roles import apply_dreamer_logic
            apply_dreamer_logic(self)
        except Exception as e:
            logger.error(f"摄梦人结算失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 检查结果
        self.check_result()

    async def sheriff_election(self):
        """完整上警竞选环节"""
        # 1. 上警阶段（10秒）
        await self.sheriff_phase()
        
        # 2. 竞选发言阶段
        sheriff_candidates = [
            user.nick for user in self.list_alive_players()
            if user.skill.get('sheriff_vote') == '上警'
        ]
        
        if not sheriff_candidates:
            self.broadcast_msg('无人上警，进入白天投票阶段', tts=True)
            self.stage = GameStage.Day
            return
        
        # 随机选第一个发言玩家
        random.shuffle(sheriff_candidates)
        first_speaker = sheriff_candidates.pop(0)
        
        # 随机顺序/逆序
        speech_order = random.choice(['顺序', '逆序'])
        if speech_order == '逆序':
            sheriff_candidates.reverse()
        
        # 排序显示
        full_list = [first_speaker] + sheriff_candidates
        full_list.sort()
        
        # 公布发言顺序
        self.broadcast_msg(
            f'上警玩家：{"".join([f"【{nick}】" for nick in full_list])}，'
            f'请【{first_speaker}】先发言，{speech_order}发言顺序。',
            tts=True
        )
        
        # 设置发言阶段
        self.stage = 'SPEECH'
        self.sheriff_speakers = full_list
        self.sheriff_speaker_index = 0
        
        # 逐个发言
        for speaker in full_list:
            self.broadcast_msg(f'请【{speaker}】发言（30秒）', tts=True)
            self.current_speaker = speaker
            await self.speech_timer(30)
            self.sheriff_speaker_index += 1
            await asyncio.sleep(2)
        
        # 清空
        self.sheriff_speakers = None
        self.sheriff_speaker_index = 0
        self.current_speaker = None
        
        # 进入投票
        self.stage = GameStage.Day
        self.broadcast_msg('竞选发言结束，准备投票', tts=True)

    async def sheriff_phase(self):
        """上警阶段"""
        self.broadcast_msg('天亮了，上警的玩家请举手（10秒）', tts=True)
        self.stage = GameStage.SHERIFF
        self.waiting = True

        async def countdown():
            for i in range(10, 0, -1):
                if not self.waiting:
                    break
                self.broadcast_msg(f'上警倒计时：{i}秒', tts=False)
                await asyncio.sleep(1)
            self.waiting = False
            self.broadcast_log_ctrl(LogCtrl.RemoveInput)

        asyncio.create_task(countdown())
        await self.wait_for_player()

    async def speech_timer(self, seconds: int):
        """单个玩家发言计时器"""
        self.waiting = True
        async def countdown():
            for i in range(seconds, 0, -1):
                if not self.waiting:
                    break
                await asyncio.sleep(1)
            self.waiting = False
            self.broadcast_log_ctrl(LogCtrl.RemoveInput)

        asyncio.create_task(countdown())
        await self.wait_for_player()

    def check_result(self, is_vote_check=False):
        """检查夜晚/投票结果，判断胜负，并触发猎人/狼王开枪"""
        out_result = []
        can_shoot_deaths = []  # 新增：记录能开枪的死亡玩家

        for nick, user in self.players.items():
            # === 梦游免疫 ===
            if user.skill.get('dream_immunity'):
                user.skill.pop('dream_immunity')
                if user.status in (PlayerStatus.PENDING_DEAD, PlayerStatus.PENDING_POISON):
                    user.status = PlayerStatus.ALIVE
                    continue

            # === 女巫毒杀：死亡 + 不能开枪 ===
            if user.status == PlayerStatus.PENDING_POISON:
                user.status = PlayerStatus.DEAD
                out_result.append(nick)
                if user.role in (Role.HUNTER, Role.WOLF_KING):
                    user.skill['can_shoot'] = False  # 毒杀无法开枪
                continue

            # === 守卫/解药救活 ===
            if user.status in [PlayerStatus.PENDING_HEAL, PlayerStatus.PENDING_GUARD]:
                user.status = PlayerStatus.ALIVE
                continue

            # === 狼刀死亡：可能能开枪 ===
            if user.status == PlayerStatus.PENDING_DEAD:
                user.status = PlayerStatus.DEAD
                out_result.append(nick)
                # 只有被狼刀死，且 can_shoot 为 True，才能开枪
                if user.role in (Role.HUNTER, Role.WOLF_KING) and user.skill.get('can_shoot', True):
                    can_shoot_deaths.append(user)
                continue

            # === 其他情况：活着 ===
            user.status = PlayerStatus.ALIVE

            # === 统计存活阵营 ===
            if user.status == PlayerStatus.ALIVE:
                if user.role in [Role.WOLF, Role.WOLF_KING]:
                    wolf_team.append(1)
                elif user.role == Role.CITIZEN:
                    citizen_team.append(1)
                else:
                    god_team.append(1)

        # 保存死亡名单
        self.death_pending = out_result

        # === 胜负判断 ===
        if not wolf_team:
            self.stop_game('好人胜利')
            return

        if not citizen_team or (not self.is_no_god() and not god_team):
            self.stop_game('狼人胜利')
            return

        # === 夜晚流程：有能开枪的死亡玩家 → 触发开枪阶段 ===
        if not is_vote_check and can_shoot_deaths:
            self.pending_shooters = can_shoot_deaths  # 临时保存
            asyncio.create_task(self.trigger_shoot_phase())
            return  # 重要：不要直接进警长选举，要先开枪！

        # === 正常进入警长选举（或白天）===
        if not is_vote_check:
            asyncio.create_task(self.sheriff_election())

    async def vote_kill(self, nick):
        self.players[nick].status = PlayerStatus.DEAD
        self.check_result(is_vote_check=True)
        if self.started:
            self.enter_null_stage()
            await self.start_game()

    async def wait_for_player(self):
        """等待玩家操作"""
        while self.waiting:
            await asyncio.sleep(0.1)
        self.broadcast_log_ctrl(LogCtrl.RemoveInput)

    def enter_null_stage(self):
        """清空当前阶段"""
        self.stage = None

    async def start_game(self):
        """开始游戏或进入下一夜"""
        if not self.started:
            if len(self.players) != len(self.roles):
                self.broadcast_msg('人数不足，无法开始游戏')
                return

            self.started = True
            self.broadcast_msg('游戏开始，请查看你的身份', tts=True)
            random.shuffle(self.roles_pool)
            for nick in self.players:
                user = self.players[nick]
                user.role = self.roles_pool.pop()
                user.status = PlayerStatus.ALIVE

                # 初始化技能
                if user.role == Role.WITCH:
                    user.skill['poison'] = True
                    user.skill['heal'] = True
                if user.role == Role.GUARD:
                    user.skill['last_protect'] = None
                if user.role == Role.DREAMER:
                    user.skill.update({
                        'last_dream_target': None,
                        'curr_dream_target': None,
                        'dreamer_nick': None,
                    })

                user.send_msg(f'你的身份是 "{user.role}"')

            await asyncio.sleep(5)

        self.logic_thread = asyncio.create_task(self.night_logic())

    def stop_game(self, reason=''):
        """结束游戏"""
        self.started = False
        self.roles_pool = copy(self.roles)
        self.round = 0
        self.enter_null_stage()
        self.waiting = False

        self.broadcast_msg(f'游戏结束，{reason}。', tts=True)
        for nick, user in self.players.items():
            self.broadcast_msg(f'{nick}：{user.role} ({user.status})')
            user.role = None
            user.status = None
            user.skill.clear()

    def list_alive_players(self) -> list:
        return [user for user in self.players.values() if user.status != PlayerStatus.DEAD]

    def list_pending_kill_players(self) -> list:
        return [user for user in self.players.values() if user.status == PlayerStatus.PENDING_DEAD]

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
