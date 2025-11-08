# roles/dreamer.py
from typing import List, Optional
from pywebio.input import actions
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Dreamer(RoleBase):
    name = '摄梦人'
    team = '好人阵营'
    can_act_at_night = True

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.DREAMER and not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        if not self.should_act():
            return []
        room = self.user.room
        buttons = [f"{u.seat}. {u.nick}" for u in room.list_alive_players() if u.nick != self.user.nick]
        return [
            actions(
                name='dreamer_team_op',
                buttons=add_cancel_button(buttons),
                help_text='摄梦人，请选择梦游对象。'
            )
        ]

    @player_action
    def select_target(self, nick: str) -> Optional[str]:
        if nick == '取消':
            return None
        if nick == self.user.nick:
            return '不能选择自己'
        target = self.user.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        self.user.skill['curr_dream_target'] = nick
        self.user.skill['acted_this_stage'] = True
        return True

    # apply_logic 保持不变
