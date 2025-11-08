# roles/seer.py
from typing import Optional
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Seer(RoleBase):
    name = '预言家'
    team = '好人阵营'
    can_act_at_night = True
    can_act_at_day = False

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.SEER

    @player_action
    def identify_player(self, nick: str) -> Optional[str]:
        target_nick = nick.split('.')[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'
        self.user.send_msg(f'玩家 {target_nick} 的身份是 {target.role}')
        return True
