# roles/base.py
from typing import Optional

from enums import GameStage
from models.user import User

def player_action(func):
    """
    玩家操作等待解锁逻辑装饰器
    """
    def wrapper(self, *args, **kwargs):
        if self.user.room is None or self.user.room.waiting is not True:
            return
        if not self.should_act():
            return

        rv = func(self, *args, **kwargs)
        if rv in [None, True]:
            self.user.room.waiting = False
        if isinstance(rv, str):
            self.user.send_msg(text=rv)

        return rv

    return wrapper

class RoleBase:
    name: str = None  # 中文名
    team: str = None  # 阵营：狼人阵营/好人阵营/第三方阵营
    can_act_at_night: bool = False  # 是否夜间行动
    can_act_at_day: bool = False  # 是否白天行动

    def __init__(self, user: User):
        self.user = user

    def should_act(self) -> bool:
        """检查是否该行动（基于阶段和角色类型）"""
        return False  # 子类重写

    def activate_skill(self, *args, **kwargs) -> Optional[str]:
        """激活技能的主方法（子类重写）"""
        return "无技能"

    @player_action
    def skip(self):
        pass
