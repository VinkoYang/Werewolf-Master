# roles/hunter.py
from typing import Optional
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Hunter(RoleBase):
    name = '猎人'
    team = '好人阵营'
    can_act_at_night = True  # 夜晚查看枪状态
    can_act_at_day = True  # 可在白天开枪（被投出时）

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.HUNTER

    @player_action
    def gun_status(self) -> Optional[str]:
        can = self.user.skill.get('can_shoot', True)
        status = "可以开枪" if can else "无法开枪"
        self.user.send_msg(f'🔫 你的开枪状态：{status}')
        return True
