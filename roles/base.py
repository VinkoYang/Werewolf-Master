# roles/base.py
import inspect
from typing import Optional, List
from pywebio.input import actions
from utils import add_cancel_button

from enums import GameStage
from models.user import User

def player_action(func):
    def wrapper(self, *args, **kwargs):
        if self.user.room is None:
            return
        if not self.should_act():
            return

        rv = func(self, *args, **kwargs)
        # 返回值约定：
        # - None: 取消/无操作 -> 结束当前等待
        # - True 或 'CONFIRMED': 最终确认，结束等待并标记为已行动
        # - 'PENDING': 临时选择，不结束等待，等待玩家点击确认
        if rv in [None, True, 'CONFIRMED']:
            self.user.skill['countdown_skip_timeout'] = True
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
    needs_global_confirm: bool = True

    def __init__(self, user: User):
        self.user = user

    def is_feared(self) -> bool:
        """检查玩家是否被梦魇恐惧，如被恐惧则当夜无法行动"""
        return self.user.skill.get('feared_this_night', False)

    def notify_fear_block(self) -> bool:
        """如玩家被恐惧，发送一次性提示并返回 True 表示行动被阻断"""
        if not self.is_feared():
            return False
        if not self.user.skill.get('fear_notified', False):
            self.user.send_msg('你被梦魇恐惧，今晚无法行动。')
            self.user.skill['fear_notified'] = True
        return True

    def should_act(self) -> bool:
        return False

    def activate_skill(self, *args, **kwargs) -> Optional[str]:
        return "无技能"

    def get_actions(self) -> List:
        """返回该角色在当前阶段应显示的 input 控件列表"""
        return []

    def input_handlers(self) -> dict:
        """返回 {input_name: handler} 映射，用于集中处理表单数据"""
        return {}

    def handle_inputs(self, data: dict):
        for key, handler in self.input_handlers().items():
            if key not in data:
                continue
            value = data.get(key)
            if value is None or handler is None:
                continue

            try:
                handler(value)
            except TypeError:
                handler()

    def supports_last_skill(self) -> bool:
        return False

    @player_action
    def skip(self):
        pass
