"""通灵师（别名魔镜少女）- 每晚查验一名玩家的具体身份，不可重复查验。"""
from typing import Optional, List

from stub import actions
from enums import PlayerStatus, GameStage
from .base import RoleBase, player_action


class MagicMirrorGirl(RoleBase):
    name = '通灵师'
    team = '好人阵营'
    can_act_at_night = True

    def input_handlers(self):
        return {'magic_mirror_op': self.verify_player}

    def should_act(self) -> bool:
        room = self.user.room
        if self.is_feared():
            return False
        return (
            self.user.status != PlayerStatus.DEAD
            and room.stage == GameStage.MAGIC_MIRROR_GIRL
            and not self.user.skill.get('acted_this_stage', False)
        )

    def get_actions(self) -> List:
        room = self.user.room
        if not room or room.stage != GameStage.MAGIC_MIRROR_GIRL:
            return []
        if self.notify_fear_block():
            return []
        if not self.should_act():
            return []

        verified: set = self.user.skill.get('verified_players', set())
        current_choice = self.user.skill.get('pending_target')
        players = sorted(room.players.values(), key=lambda x: x.seat or 0)

        buttons = []
        for u in players:
            label = f"{u.seat}. {u.nick}"
            if u.nick == self.user.nick or u.status == PlayerStatus.DEAD or u.nick in verified:
                buttons.append({'label': label, 'value': label, 'disabled': True, 'color': 'secondary'})
            elif u.nick == current_choice:
                buttons.append({'label': label, 'value': label, 'color': 'warning'})
            else:
                buttons.append({'label': label, 'value': label})

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [actions(name='magic_mirror_op', buttons=buttons, help_text='通灵师，请查验一名玩家的具体身份。')]

    @player_action
    def verify_player(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            self.user.skill.pop('pending_target', None)
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃查验。')
            return True

        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        verified: set = self.user.skill.get('verified_players', set())
        if target_nick in verified:
            return '不可重复查验同一玩家'

        self.user.skill['pending_target'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        target_nick = self.user.skill.pop('pending_target', None)
        if not target_nick:
            return '未选择目标'
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        role_inst = getattr(target, 'role_instance', None)
        if role_inst and hasattr(role_inst, 'get_apparent_role'):
            role_name = role_inst.get_apparent_role().value
        else:
            role_name = target.role.value if target.role else '未知'

        verified: set = self.user.skill.setdefault('verified_players', set())
        verified.add(target_nick)

        self.user.send_msg(f'你查验了{target.seat}号玩家，他的身份是：{role_name}')
        self.user.skill['acted_this_stage'] = True
        return True

    @player_action
    def skip(self):
        self.user.skill.pop('pending_target', None)
        self.user.skill['acted_this_stage'] = True
        self.user.send_msg('今夜，你放弃查验。')
        if self.user.room:
            self.user.room.waiting = False
