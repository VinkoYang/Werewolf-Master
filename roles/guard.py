# roles/guard.py
from typing import Optional
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, GuardRule

class Guard(RoleBase):
    name = '守卫'
    team = '好人阵营'
    can_act_at_night = True
    can_act_at_day = False

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.GUARD

    @player_action
    def protect_player(self, nick: str) -> Optional[str]:
        if self.user.skill.get('last_protect') == nick:
            return '两晚不可守卫同一玩家'

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
        return True
