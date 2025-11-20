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
        
    def get_actions(self):
            if self.user.room.stage == GameStage.HUNTER and self.user.skill.get('can_shoot', False):
                # 猎人开枪行为可被确认
                return [
                    actions(name='hunter_team_op', buttons=['开枪', '放弃'], help_text='猎人开枪')
                ]
            return []

    @player_action
    def gun_status(self) -> Optional[str]:
        can = self.user.skill.get('can_shoot', True)
        status = "可以开枪" if can else "无法开枪"
        self.user.send_msg(f'🔫 你的开枪状态：{status}')
        return True

    @player_action
    def kill_confirm(self, nick: str) -> Optional[str]:
        # 处理开枪按钮（简化：立即开枪或放弃）
        if nick == '放弃':
            return None
        if not self.user.skill.get('can_shoot', False):
            return '无法开枪'
        # 标记为已行动并在外层流程处理猎人开枪逻辑（在房间结算时触发）
        self.user.skill['acted_this_stage'] = True
        # 可以在此触发立即开枪逻辑（使用现有 send_msg 提示）
        self.user.send_msg('你选择了开枪（请实现开枪目标选择逻辑）')
        return True
        
