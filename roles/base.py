# roles/base.py
from typing import Optional, List
from pywebio.input import actions
from utils import add_cancel_button

from enums import GameStage
from models.user import User

def player_action(func):
    def wrapper(self, *args, **kwargs):
        if self.user.room is None or self.user.room.waiting is not True:
            return
        if not self.should_act():
            return

        rv = func(self, *args, **kwargs)
        # 返回值约定：
        # - None: 取消/无操作 -> 结束当前等待
        # - True 或 'CONFIRMED': 最终确认，结束等待并标记为已行动
        # - 'PENDING': 临时选择，不结束等待，等待玩家点击确认
        if rv in [None, True, 'CONFIRMED']:
            self.user.room.waiting = False
        if isinstance(rv, str) and rv not in ['PENDING', 'CONFIRMED']:
            self.user.send_msg(text=rv)
        return rv
    return wrapper

class RoleBase:
    name: str = None
    team: str = None
    can_act_at_night: bool = False
    can_act_at_day: bool = False

    def __init__(self, user: User):
        self.user = user

    def should_act(self) -> bool:
        return False

    def activate_skill(self, *args, **kwargs) -> Optional[str]:
        return "无技能"

    def get_actions(self) -> List:
        """返回该角色在当前阶段应显示的 input 控件列表"""
        return []

    @player_action
    def skip(self):
        pass
