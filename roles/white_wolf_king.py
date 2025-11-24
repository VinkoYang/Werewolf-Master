# roles/white_wolf_king.py
from typing import List
from pywebio.input import actions

from enums import GameStage, PlayerStatus
from .wolf import Wolf

class WhiteWolfKing(Wolf):
    name = '白狼王'
    team = '狼人阵营'

    def input_handlers(self):
        handlers = super().input_handlers()
        handlers.update({'white_bomb_target': self.set_bomb_target})
        return handlers

    def get_actions(self) -> List:
        base_actions = list(super().get_actions())
        if self._can_configure_bomb_target():
            base_actions.append(self._build_bomb_selector())
        return base_actions

    def _can_configure_bomb_target(self) -> bool:
        room = self.user.room
        if not room or self.user.status != PlayerStatus.ALIVE:
            return False
        if room.stage == GameStage.WOLF and self.should_act():
            return False  # 夜间杀人阶段沿用普通狼人操作
        return room.can_wolf_self_bomb(self.user)

    def _build_bomb_selector(self):
        room = self.user.room
        players = sorted(room.list_alive_players(), key=lambda u: u.seat or 0)
        current = self.user.skill.get('white_wolf_bomb_target')
        buttons = []
        for player in players:
            if player.nick == self.user.nick:
                continue
            label = f"{player.seat}. {player.nick}"
            btn = {'label': label, 'value': label}
            if player.nick == current:
                btn['color'] = 'danger'
            buttons.append(btn)
        buttons.append({'label': '清除目标', 'value': 'clear', 'color': 'secondary'})
        return actions(
            name='white_bomb_target',
            buttons=buttons,
            help_text='白狼王：选择自爆时要带走的玩家'
        )

    def set_bomb_target(self, choice: str):
        room = self.user.room
        if not room or not self._can_configure_bomb_target():
            return
        if choice == 'clear':
            self.user.skill.pop('white_wolf_bomb_target', None)
            self.user.send_msg('已清除自爆击杀目标。')
            return
        nick = choice.split('.', 1)[-1].strip()
        target = room.players.get(nick)
        if not target or target.status != PlayerStatus.ALIVE or target.nick == self.user.nick:
            self.user.send_msg('目标无效，请重新选择。')
            return
        self.user.skill['white_wolf_bomb_target'] = target.nick
        seat = target.seat if target.seat is not None else '?'
        self.user.send_msg(f'自爆时将带走{seat}号{target.nick}。')
