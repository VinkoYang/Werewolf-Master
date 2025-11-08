# roles/witch.py
from typing import Optional
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, WitchRule

class Witch(RoleBase):
    name = '女巫'
    team = '好人阵营'
    can_act_at_night = True
    can_act_at_day = False

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.WITCH

    def has_heal(self) -> bool:
        return self.user.skill.get('heal', False)

    def has_poison(self) -> bool:
        return self.user.skill.get('poison', False)

    @player_action
    def heal_player(self, nick: str) -> Optional[str]:
        room = self.user.room
        if room.witch_rule == WitchRule.NO_SELF_RESCUE and nick == self.user.nick:
            return '不能解救自己'
        if room.witch_rule == WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY:
            if nick == self.user.nick and room.round != 1:
                return '仅第一晚可以解救自己'

        if not self.has_heal():
            return '没有解药了'

        target = room.players.get(nick)
        if not target:
            return '查无此人'

        if target.status != PlayerStatus.PENDING_DEAD:
            return '此人未被刀'

        target.status = PlayerStatus.PENDING_HEAL
        self.user.skill['heal'] = False
        return True

    @player_action
    def kill_player(self, nick: str) -> Optional[str]:
        if not self.has_poison():
            return '没有毒药了'
        target_nick = nick.split('.')[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        target.status = PlayerStatus.PENDING_POISON
        self.user.skill['poison'] = False
        return True
