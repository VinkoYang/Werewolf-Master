# models/user.py
import asyncio
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING, Any

from pywebio import run_async
from pywebio.output import output
from pywebio.session import get_current_session
from pywebio.session.coroutinebased import TaskHandle

from enums import Role, PlayerStatus, LogCtrl
from models.system import Config, Global
from stub import OutputHandler
from . import logger

if TYPE_CHECKING:
    from .room import Room
    from roles.base import RoleBase   # 引入角色基类


@dataclass
class User:
    nick: str
    main_task_id: Any
    input_blocking: bool = False

    room: Optional['Room'] = None
    role: Optional[Role] = None
    role_instance: Optional['RoleBase'] = None   # 具体角色实例
    skill: dict = None
    status: Optional[PlayerStatus] = None
    seat: Optional[int] = None

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

    def send_msg(self, text: str):
        """私聊消息"""
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

    # ------------------------------------------------------------------
    # 统一的 skip（交给角色实例处理）
    # ------------------------------------------------------------------
    def skip(self):
        if self.role_instance:
            self.role_instance.skip()

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
            role_instance=None,
            skill=None,
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
