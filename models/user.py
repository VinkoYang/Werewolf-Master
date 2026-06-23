# models/user.py
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING, Any

from enums import Role, PlayerStatus, LogCtrl
from models.system import Config, Global
from . import logger

if TYPE_CHECKING:
    from .room import Room
    from roles.base import RoleBase


@dataclass
class User:
    nick: str
    sid: Optional[str]          # current Socket.IO session id; None = disconnected
    reconnect_token: str        # stored in browser localStorage for reconnection

    room: Optional['Room'] = None
    role: Optional[Role] = None
    role_instance: Optional['RoleBase'] = None
    skill: dict = None
    status: Optional[PlayerStatus] = None
    seat: Optional[int] = None

    message_cursor: int = 0     # next index in room.log to deliver to this user
    input_blocking: bool = False

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
                'acted_this_stage': False,
                'last_words_skill_resolved': False,
                'last_words_done': False,
                'pending_last_skill': False,
                'exile_vote_pending': False,
                'exile_has_balloted': False,
            }

    def send_msg(self, text: str):
        if self.room:
            self.room.send_msg(text, nick=self.nick)
        else:
            logger.warning('在玩家非进入房间状态时调用了 User.send_msg()')

    def get_pending_messages(self) -> list:
        """Return log entries since message_cursor that this user should see, then advance cursor."""
        if not self.room:
            return []
        msgs = []
        log = self.room.log
        for sender, content in log[self.message_cursor:]:
            if sender == self.nick:
                # Private message for this player
                msgs.append({'type': 'private', 'text': str(content)})
            elif sender == Config.SYS_NICK:
                # Public broadcast
                if isinstance(content, dict):
                    msgs.append({'type': 'public', 'text': content.get('text', ''), 'tts': bool(content.get('tts'))})
                else:
                    msgs.append({'type': 'public', 'text': str(content), 'tts': False})
            elif sender is None and content == LogCtrl.RemoveInput:
                msgs.append({'type': 'cancel_input'})
            # else: private message addressed to another user – skip
        self.message_cursor = len(log)
        return msgs

    def skip(self, reason: Optional[str] = None):
        self.skill['skip_reason'] = reason
        if self.role_instance:
            self.role_instance.skip()

    @classmethod
    def validate_nick(cls, nick) -> Optional[str]:
        if not nick:
            return '昵称不能为空'
        if len(nick) > 16:
            return '昵称不能超过16个字符'
        if nick in Global.users or Config.SYS_NICK in nick:
            return '昵称已被使用'

    @classmethod
    def alloc(cls, nick: str, sid: str, reconnect_token: str) -> 'User':
        if nick in Global.users:
            raise ValueError('用户已存在')
        user = cls(nick=nick, sid=sid, reconnect_token=reconnect_token)
        Global.users[nick] = user
        logger.info(f'用户 "{nick}" 登录')
        return user

    @classmethod
    def free(cls, user: 'User'):
        Global.users.pop(user.nick, None)
        if user.room:
            user.room.remove_player(user)
        logger.info(f'用户 "{user.nick}" 注销')
