# roles/wolf_king.py
from typing import Optional, List
from pywebio.input import actions

from enums import PlayerStatus, GameStage
from .base import player_action
from .wolf import Wolf


class WolfKing(Wolf):
    name = '狼王'
    team = '狼人阵营'
    can_act_at_night = True
    can_act_at_day = True

    def input_handlers(self):
        handlers = super().input_handlers()
        handlers.update({
            'wolfking_confirm': self.confirm,
            'wolfking_shoot_target': self.select_shoot_target,
            'wolfking_shoot_confirm': self.confirm_shoot,
        })
        return handlers

    def should_act(self) -> bool:
        room = self.user.room
        if not room:
            return False
        if room.stage == GameStage.WOLF:
            return super().should_act()
        return (
            room.stage == GameStage.WOLF_KING and
            self.user.status != PlayerStatus.DEAD and
            not self.user.skill.get('acted_this_stage', False)
        )

    def get_actions(self) -> List:
        if self.in_shoot_mode():
            return self.get_shoot_actions()

        room = self.user.room
        if room and room.stage == GameStage.WOLF:
            return super().get_actions()

        if room and room.stage == GameStage.WOLF_KING and self.should_act():
            if not self.user.skill.get('wolfking_msg_sent', False):
                can_shoot = self.user.skill.get('can_shoot', True)
                status_msg = "可以开枪" if can_shoot else "不可以开枪"
                self.user.send_msg(f'🔫 你的开枪状态：{status_msg}')
                self.user.skill['wolfking_msg_sent'] = True
            return [
                actions(
                    name='wolfking_confirm',
                    buttons=['确认'],
                    help_text='点击确认结束狼王阶段'
                )
            ]
        return []

    @player_action
    def confirm(self) -> Optional[str]:
        self.user.skill['acted_this_stage'] = True
        self.user.skill.pop('wolfking_msg_sent', None)
        return True

    def supports_last_skill(self) -> bool:
        return True

    def in_shoot_mode(self) -> bool:
        room = self.user.room
        if not room or room.stage != GameStage.LAST_WORDS:
            return False
        day_state = getattr(room, 'day_state', {})
        if day_state.get('current_last_word') != self.user.nick:
            return False
        return self.user.skill.get('pending_last_skill', False) and self.user.skill.get('can_shoot', False)

    def get_shoot_actions(self) -> List:
        room = self.user.room
        buttons = []
        alive_players = sorted(room.list_alive_players(), key=lambda u: u.seat or 0)
        pending_choice = self.user.skill.get('wolfking_pending_shot')
        for player in alive_players:
            if player.nick == self.user.nick:
                continue
            label = f"{player.seat}. {player.nick}"
            btn = {'label': label, 'value': label}
            if pending_choice == player.nick:
                btn['color'] = 'danger'
            buttons.append(btn)
        buttons.append({'label': '放弃开枪', 'value': 'cancel_shot', 'color': 'secondary'})
        inputs: List = [
            actions(
                name='wolfking_shoot_target',
                buttons=buttons,
                help_text='请选择要带走的玩家'
            )
        ]
        if pending_choice:
            inputs.append(
                actions(
                    name='wolfking_shoot_confirm',
                    buttons=[{'label': '确认击杀', 'value': 'confirm', 'color': 'danger'}],
                    help_text='确认执行击杀'
                )
            )
        return inputs

    def select_shoot_target(self, value: str):
        if not self.in_shoot_mode():
            return
        if value == 'cancel_shot':
            self.user.skill['pending_last_skill'] = False
            self.user.skill['last_words_skill_resolved'] = True
            self.user.skill['wolfking_pending_shot'] = None
            self.user.skill['can_shoot'] = False
            self.user.send_msg('你放弃了开枪')
            return
        target_nick = value.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target or target.status != PlayerStatus.ALIVE:
            self.user.send_msg('目标不可用')
            return
        if target.nick == self.user.nick:
            self.user.send_msg('不能击杀自己')
            return
        self.user.skill['wolfking_pending_shot'] = target.nick
        self.user.send_msg(f'已选择 {target_nick} 作为目标，点击确认击杀')

    def confirm_shoot(self, action: str):
        if not self.in_shoot_mode() or action != 'confirm':
            return
        target_nick = self.user.skill.pop('wolfking_pending_shot', None)
        if not target_nick:
            self.user.send_msg('未选择目标')
            return
        room = self.user.room
        target = room.players.get(target_nick)
        if not target or target.status != PlayerStatus.ALIVE:
            self.user.send_msg('目标不可用')
            return
        seat = target.seat if target.seat is not None else '?'
        from_day_execution = (
            room.stage == GameStage.LAST_WORDS and
            room.day_state.get('after_last_words') == 'day_skill_to_speech'
        )
        room.handle_last_word_skill_kill(target.nick, from_day_execution=from_day_execution)
        room.broadcast_msg(f'{seat}号玩家被带走')
        self.user.skill['pending_last_skill'] = False
        self.user.skill['last_words_skill_resolved'] = True
        self.user.skill['can_shoot'] = False
        room.advance_last_words_progress(self.user)
