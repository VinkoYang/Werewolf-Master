# roles/wolf.py
from typing import List, Optional
from pywebio.input import actions
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import GameStage, PlayerStatus

class Wolf(RoleBase):
    name = '狼人'
    team = '狼人阵营'
    can_act_at_night = True

    def should_act(self) -> bool:
        room = self.user.room
        return (
            self.user.status != PlayerStatus.DEAD and
            room.stage == GameStage.WOLF and
            not self.user.skill.get('acted_this_stage', False)
        )

    def get_actions(self) -> List:
        if not self.should_act():
            return []

        room = self.user.room
        alive_players = room.list_alive_players()
        buttons = [f"{u.seat}. {u.nick}" for u in alive_players if u.nick != self.user.nick]

        return [
            actions(
                name='wolf_team_op',
                buttons=add_cancel_button(buttons),
                help_text='狼人，请选择要击杀的对象。'
            )
        ]

    @player_action
    def kill_player(self, nick: str) -> Optional[str]:
        if nick == '取消':
            return None

        self.user.skill['acted_this_stage'] = True

        room = self.user.room
        room.skill.setdefault('wolf_kill', set()).add(nick)
        return True
