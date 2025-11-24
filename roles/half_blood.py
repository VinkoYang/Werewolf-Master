# roles/half_blood.py
import random
from typing import List, Optional
from pywebio.input import actions

from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, Role

class HalfBlood(RoleBase):
    name = '混血儿'
    team = '特殊阵营'
    can_act_at_night = True

    def input_handlers(self):
        return {'half_blood_choice': self.select_relative}

    def should_act(self) -> bool:
        room = self.user.room
        if not room or self.user.status == PlayerStatus.DEAD:
            return False
        if room.stage != GameStage.HALF_BLOOD or room.round != 1:
            return False
        if self.user.skill.get('half_blood_completed'):
            return False
        return not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        if not self.should_act():
            return []
        room = self.user.room
        current = self.user.skill.get('pending_half_blood_target')
        players = sorted(room.players.values(), key=lambda u: u.seat or 0)
        buttons = []
        for candidate in players:
            label = f"{candidate.seat}. {candidate.nick}"
            btn = {'label': label, 'value': label}
            if candidate.nick == self.user.nick or candidate.status == PlayerStatus.DEAD:
                btn['disabled'] = True
                btn['color'] = 'secondary'
            elif candidate.nick == current:
                btn['color'] = 'warning'
            buttons.append(btn)
        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='half_blood_choice',
                buttons=buttons,
                help_text='混血儿，请在第一夜选择一位血亲。'
            )
        ]

    def _valid_targets(self):
        room = self.user.room
        if not room:
            return []
        return [
            u for u in room.players.values()
            if u.nick != self.user.nick and u.status != PlayerStatus.DEAD
        ]

    @player_action
    def select_relative(self, choice: str) -> Optional[str]:
        if choice in ('取消', '放弃'):
            return self.skip()
        nick = choice.split('.', 1)[-1].strip()
        target = self.user.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD or target.nick == self.user.nick:
            return '目标无效'
        self.user.skill['pending_half_blood_target'] = target.nick
        seat = target.seat if target.seat is not None else '?'
        self.user.send_msg(f'你选择了{seat}号{target.nick}作为血亲，请确认。')
        return 'PENDING'

    def _finalize_choice(self, target, auto: bool = False) -> Optional[str]:
        self.user.skill.pop('pending_half_blood_target', None)
        if not target:
            self.user.skill['half_blood_camp'] = 'good'
            self.user.skill['half_blood_completed'] = True
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('系统未能为你指定血亲，系统将按默认规则处理。')
            return True
        camp = 'wolf' if target.role in (Role.WOLF, Role.WOLF_KING) else 'good'
        self.user.skill['half_blood_camp'] = camp
        self.user.skill['half_blood_target'] = target.nick
        self.user.skill['half_blood_completed'] = True
        self.user.skill['acted_this_stage'] = True
        seat = target.seat if target.seat is not None else '?'
        prefix = '系统自动确认' if auto else '你认定'
        self.user.send_msg(f'{prefix}{seat}号{target.nick}为血亲。')
        return True

    @player_action
    def confirm(self) -> Optional[str]:
        nick = self.user.skill.pop('pending_half_blood_target', None)
        if not nick:
            return '未选择目标'
        target = self.user.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标无效'
        return self._finalize_choice(target)

    def _auto_pick_target(self):
        candidates = self._valid_targets()
        if not candidates:
            return None
        return random.choice(candidates)

    def ensure_choice(self):
        if self.user.skill.get('half_blood_completed'):
            return
        target = self.user.skill.get('pending_half_blood_target')
        if target:
            player = self.user.room.players.get(target)
        else:
            player = self._auto_pick_target()
        if player:
            self._finalize_choice(player, auto=target is None)
        else:
            self._finalize_choice(None, auto=True)

    @player_action
    def skip(self):
        if self.user.skill.get('half_blood_completed'):
            return True
        target = self.user.skill.get('pending_half_blood_target')
        if target:
            player = self.user.room.players.get(target)
            return self._finalize_choice(player, auto=True)
        player = self._auto_pick_target()
        if player:
            return self._finalize_choice(player, auto=True)
        return self._finalize_choice(None, auto=True)
