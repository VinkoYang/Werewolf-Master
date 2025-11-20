# roles/guard.py
from typing import Optional, List
from pywebio.input import actions
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, GuardRule

class Guard(RoleBase):
    name = '守卫'
    team = '好人阵营'
    can_act_at_night = True

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.GUARD and not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        if not self.should_act():
            return []
        room = self.user.room
        last = self.user.skill.get('last_protect')
        buttons = [f"{u.seat}. {u.nick}" for u in room.list_alive_players() if u.nick != last]
        return [
            actions(
                name='guard_team_op',
                buttons=add_cancel_button(buttons),
                help_text='守卫，请选择守护对象（不能连续守护同一人）。'
            )
        ]

    @player_action
    def protect_player(self, nick: str) -> Optional[str]:
        if nick == '取消':
            return None
        if self.user.skill.get('last_protect') == nick:
            return '两晚不可守卫同一玩家'

        target = self.user.room.players.get(nick)
        if not target:
            return '查无此人'

        # 暂存守护目标，等待确认
        self.user.skill['pending_protect'] = nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        nick = self.user.skill.pop('pending_protect', None)
        if nick is None:
            return '未选择目标'
        if self.user.skill.get('last_protect') == nick:
            return '两晚不可守护同一玩家'
        target = self.user.room.players.get(nick)
        if not target:
            return '查无此人'
        if target.status == PlayerStatus.PENDING_POISON:
            return '守卫无法防御毒药'
        if target.status == PlayerStatus.PENDING_HEAL and self.user.room.guard_rule == GuardRule.MED_CONFLICT:
            target.status = PlayerStatus.PENDING_DEAD
            return '守救冲突，目标死亡'

        target.status = PlayerStatus.PENDING_GUARD
        self.user.skill['last_protect'] = nick
        self.user.skill['acted_this_stage'] = True
        return True
