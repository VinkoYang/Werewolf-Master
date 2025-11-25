# roles/nine_tailed_fox.py
from typing import List
from pywebio.input import actions

from enums import GameStage, PlayerStatus, Role
from .base import RoleBase, player_action


class NineTailedFox(RoleBase):
    name = '九尾妖狐'
    team = '神阵营'
    can_act_at_night = True

    GOD_ROLES = {
        Role.SEER,
        Role.WITCH,
        Role.GUARD,
        Role.HUNTER,
        Role.DREAMER,
        Role.IDIOT,
        Role.HALF_BLOOD,
    }
    VILLAGER_ROLES = {Role.CITIZEN}

    def __init__(self, user):
        super().__init__(user)
        self.user.skill.setdefault('tails_remaining', 9)

    def input_handlers(self):
        return {'nine_tail_ack': self.acknowledge}

    def should_act(self) -> bool:
        room = self.user.room
        return bool(
            room and
            self.user.status != PlayerStatus.DEAD and
            room.stage == GameStage.NINE_TAILED_FOX and
            not self.user.skill.get('acted_this_stage', False)
        )

    def get_actions(self) -> List:
        if not self.should_act():
            return []
        # 预览阶段需要包含即将死亡的单位
        self.refresh_tail_state(include_pending=True, register_death=False)
        tails = self.user.skill.get('tails_remaining', 9)
        if not self.user.skill.get('fox_tail_notified'):
            self.user.send_msg(f'🌙 九尾妖狐睁眼，本轮尾巴数：{tails}/9')
            self.user.skill['fox_tail_notified'] = True
        help_text = f"当前尾巴数：{tails}/9。尾巴耗尽会立即死亡。"
        button_color = 'danger' if tails <= 3 else 'primary'
        return [
            actions(
                name='nine_tail_ack',
                buttons=[{'label': '明白', 'value': 'ack', 'color': button_color}],
                help_text=help_text
            )
        ]

    @player_action
    def acknowledge(self, _value: str):
        self.user.skill['acted_this_stage'] = True
        self.user.skill.pop('fox_tail_notified', None)
        return True

    def refresh_tail_state(self, *, include_pending: bool = False, register_death: bool = True):
        room = self.user.room
        if not room:
            return
        statuses = {PlayerStatus.DEAD}
        if include_pending:
            statuses.update({PlayerStatus.PENDING_DEAD, PlayerStatus.PENDING_POISON})
        tail_loss = 0
        for player in room.players.values():
            if player.nick == self.user.nick:
                continue
            if player.role in self.GOD_ROLES and player.status in statuses:
                tail_loss += 2
            elif player.role in self.VILLAGER_ROLES and player.status in statuses:
                tail_loss += 1
        tails = max(0, 9 - tail_loss)
        self.user.skill['tails_remaining'] = tails
        if not register_death or tails > 0 or self.user.status == PlayerStatus.DEAD:
            return
        self.user.status = PlayerStatus.DEAD
        seat = self.user.seat if self.user.seat is not None else '?'
        room.broadcast_msg(f'{seat}号{self.user.nick}失去所有尾巴，悄然离场。')
        if room.stage != GameStage.Day:
            pending = getattr(room, 'death_pending', [])
            if self.user.nick not in pending:
                pending.append(self.user.nick)