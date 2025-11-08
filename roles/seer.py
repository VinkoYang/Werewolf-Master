# roles/seer.py
from typing import Optional, List
from pywebio.input import actions
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Seer(RoleBase):
    name = '预言家'
    team = '好人阵营'
    can_act_at_night = True

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.SEER and not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        if not self.should_act():
            return []
        room = self.user.room
        buttons = [f"{u.seat}. {u.nick}" for u in room.list_alive_players() if u.nick != self.user.nick]
        return [
            actions(
                name='seer_team_op',
                buttons=add_cancel_button(buttons),
                help_text='预言家，请查验身份。'
            )
        ]

    @player_action
    def identify_player(self, nick: str) -> Optional[str]:
        if nick == '取消':
            return None
        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'
        self.user.send_msg(f'玩家 {target_nick} 的身份是 {target.role}')
        self.user.skill['acted_this_stage'] = True
        return True
