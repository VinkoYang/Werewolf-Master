# roles/hunter.py
from typing import Optional, List
from pywebio.input import actions
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Hunter(RoleBase):
    name = '猎人'
    team = '好人阵营'
    can_act_at_night = True  # 夜晚查看枪状态
    can_act_at_day = True  # 可在白天开枪（被投出时）
    needs_global_confirm = False

    def input_handlers(self):
        return {'hunter_confirm': self.confirm}

    def should_act(self) -> bool:
        room = self.user.room
        return (self.user.status != PlayerStatus.DEAD and 
                room.stage == GameStage.HUNTER and 
                not self.user.skill.get('acted_this_stage', False))
        
    def get_actions(self) -> List:
        if not self.should_act():
            return []
        
        # 猎人睡眼时，发送开枪状态私聊消息 - 只发送一次
        if not self.user.skill.get('hunter_msg_sent', False):
            can_shoot = self.user.skill.get('can_shoot', True)
            status_msg = "可以开枪" if can_shoot else "不可以开枪"
            self.user.send_msg(f'🔫 你的开枪状态：{status_msg}')
            self.user.skill['hunter_msg_sent'] = True
        
        # 添加确认按键
        return [
            actions(
                name='hunter_confirm',
                buttons=['确认'],
                help_text='点击确认结束你的回合'
            )
        ]

    @player_action
    def confirm(self) -> Optional[str]:
        # 猎人夜晚只是查看状态，标记为已行动即可
        self.user.skill['acted_this_stage'] = True
        # 清理消息发送标志
        self.user.skill.pop('hunter_msg_sent', None)
        return True
