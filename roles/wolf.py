# roles/wolf.py
from typing import Optional
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Wolf(RoleBase):
    name = '狼人'
    team = '狼人阵营'
    can_act_at_night = True
    can_act_at_day = False

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.WOLF

    @player_action
    def kill_player(self, nick: str) -> Optional[str]:
        if nick == '取消':
            return None
        target_nick = nick.split('.')[-1].strip()
        if target_nick == self.user.nick:
            return '不能击杀自己'
        target = self.user.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        target.status = PlayerStatus.PENDING_DEAD
        self.user.send_msg(f'你选择了击杀 {target_nick}')
        return True
