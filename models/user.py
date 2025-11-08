# models/user.py
import asyncio
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING, Any

from pywebio import run_async
from pywebio.output import output
from pywebio.session import get_current_session
from pywebio.session.coroutinebased import TaskHandle

from enums import Role, PlayerStatus, LogCtrl, WitchRule, GuardRule, GameStage
from models.system import Config, Global
from stub import OutputHandler
from . import logger

if TYPE_CHECKING:
    from .room import Room


def player_action(func):
    """
    玩家操作等待解锁逻辑装饰器
    """
    def wrapper(self: 'User', *args, **kwargs):
        if self.room is None or self.room.waiting is not True:
            return
        if not self.should_act():
            return

        rv = func(self, *args, **kwargs)
        if rv in [None, True]:
            self.room.waiting = False
            #self.room.enter_null_stage()
        if isinstance(rv, str):
            self.send_msg(text=rv)

        return rv

    return wrapper


@dataclass
class User:
    nick: str
    main_task_id: Any
    input_blocking: bool = False

    room: Optional['Room'] = None
    role: Optional[Role] = None
    skill: dict = None
    status: Optional[PlayerStatus] = None
    seat: Optional[int] = None  # Add this

    game_msg: OutputHandler = None
    game_msg_syncer: Optional[TaskHandle] = None

    def __post_init__(self):
        if self.skill is None:
            self.skill = {
                'heal': False,
                'poison': False,
                'last_protect': None,
                'can_shoot': True,
                'dream_immunity': False,
                'last_dream_target': None,
                'curr_dream_target': None,
                'dreamer_nick': None,
                'sheriff_vote': None,
            }
        if self.game_msg is None:
            self.game_msg = output()

    def __str__(self):
        return self.nick

    __repr__ = __str__

    def send_msg(self, text):
        if self.room:
            self.room.send_msg(text, nick=self.nick)
        else:
            logger.warning('在玩家非进入房间状态时调用了 User.send_msg()')

    async def _game_msg_syncer(self):
        last_idx = len(self.room.log) if self.room else 0
        while True:
            if not self.room:
                break
            for msg in self.room.log[last_idx:]:
                if msg[0] == self.nick:
                    self.game_msg.append(f'Private: {msg[1]}')
                elif msg[0] == Config.SYS_NICK:
                    self.game_msg.append(f'Public: {msg[1]}')
                elif msg[0] is None:
                    if msg[1] == LogCtrl.RemoveInput and self.input_blocking:
                        get_current_session().send_client_event({
                            'event': 'from_cancel',
                            'task_id': self.main_task_id,
                            'data': None
                        })

            if len(self.room.log) > 50000:
                self.room.log = self.room.log[len(self.room.log) // 2:]
            last_idx = len(self.room.log)
            await asyncio.sleep(0.2)

    def start_syncer(self):
        if self.game_msg_syncer is not None:
            raise AssertionError
        self.game_msg_syncer = run_async(self._game_msg_syncer())

    def stop_syncer(self):
        if self.game_msg_syncer is None or self.game_msg_syncer.closed():
            raise AssertionError
        self.game_msg_syncer.close()
        self.game_msg_syncer = None

    def should_act(self):
        stage_map = {
            GameStage.Day: [],
            GameStage.GUARD: [Role.GUARD],
            GameStage.WITCH: [Role.WITCH],
            GameStage.HUNTER: [Role.HUNTER],
            GameStage.SEER: [Role.SEER],
            GameStage.WOLF: [Role.WOLF, Role.WOLF_KING],
            GameStage.DREAMER: [Role.DREAMER],
            # ... (assuming other stages if needed)
        }
        return self.status != PlayerStatus.DEAD and self.role in stage_map.get(self.room.stage, [])

    def witch_has_heal(self) -> bool:
        return self.skill.get('heal', False)

    def witch_has_poison(self) -> bool:
        return self.skill.get('poison', False)

    @player_action
    def skip(self):
        pass


    @player_action
    def wolf_kill_player(self, nick):
        if nick == '取消':
            return None  # Skip without error, but end phase for single-player; for multi, no effect
        target_nick = nick.split('.')[-1].strip()
        if target_nick == self.nick:
            return '不能击杀自己'
        target = self.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        target.status = PlayerStatus.PENDING_DEAD
        self.send_msg(f'你选择了击杀 {target_nick}')
        return True  # 必须返回 True

    @player_action
    def seer_identify_player(self, nick):
        target_nick = nick.split('.')[-1].strip()
        target = self.room.players.get(target_nick)
        if not target:
            return '查无此人'
        self.send_msg(f'玩家 {target_nick} 的身份是 {target.role}')
        return True  # 必须返回 True

    @player_action
    def witch_kill_player(self, nick):
        if not self.witch_has_poison():
            return '没有毒药了'
        target_nick = nick.split('.')[-1].strip()
        target = self.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        target.status = PlayerStatus.PENDING_POISON
        self.skill['poison'] = False
        return True  # 必须返回 True

    @player_action
    def witch_heal_player(self, nick):
        if self.room.witch_rule == WitchRule.NO_SELF_RESCUE and nick == self.nick:
            return '不能解救自己'
        if self.room.witch_rule == WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY:
            if nick == self.nick and self.room.round != 1:
                return '仅第一晚可以解救自己'

        if not self.witch_has_heal():
            return '没有解药了'

        target = self.room.players.get(nick)
        if not target:
            return '查无此人'

        # 只有 PENDING_DEAD 才能救
        if target.status != PlayerStatus.PENDING_DEAD:
            return '此人未被刀'

        target.status = PlayerStatus.PENDING_HEAL
        self.skill['heal'] = False
        return True  # 必须返回 True

    @player_action
    def guard_protect_player(self, nick):
        if self.skill.get('last_protect') == nick:
            return '两晚不可守卫同一玩家'

        target = self.room.players.get(nick)
        if not target:
            return '查无此人'

        if target.status == PlayerStatus.PENDING_POISON:
            return '守卫无法防御毒药'

        if target.status == PlayerStatus.PENDING_HEAL and self.room.guard_rule == GuardRule.MED_CONFLICT:
            target.status = PlayerStatus.PENDING_DEAD
            return '守救冲突，目标死亡'

        target.status = PlayerStatus.PENDING_GUARD
        self.skill['last_protect'] = nick
        return True  # 必须返回 True

    @player_action  # 保留装饰器，但因为 room.waiting=False，不会阻塞
    def hunter_gun_status(self):
        can = self.skill.get('can_shoot', True)
        status = "可以开枪" if can else "无法开枪"
        self.send_msg(f'🔫 你的开枪状态：{status}')
        return True

    @player_action
    def dreamer_select(self, nick):
        if nick == self.nick:
            return '不能选择自己'
        target = self.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        self.skill['curr_dream_target'] = nick
        return True  # 必须返回 True

    @classmethod
    def validate_nick(cls, nick) -> Optional[str]:
        if nick in Global.users or Config.SYS_NICK in nick:
            return '昵称已被使用'

    @classmethod
    def alloc(cls, nick, init_task_id) -> 'User':
        if nick in Global.users:
            raise ValueError("用户已存在")

        user = cls(
            nick=nick,
            main_task_id=init_task_id,
            input_blocking=False,
            room=None,
            role=None,
            skill=None,  # __post_init__ 会初始化
            status=None,
            game_msg=None,
            game_msg_syncer=None
        )
        Global.users[nick] = user
        logger.info(f'用户 "{nick}" 登录')
        return user

    @classmethod
    def free(cls, user: 'User'):
        Global.users.pop(user.nick, None)
        if user.room:
            user.room.remove_player(user)
        logger.info(f'用户 "{user.nick}" 注销')
