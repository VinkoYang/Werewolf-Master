# roles/hunter.py
from typing import Optional, List
from pywebio.input import actions
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Hunter(RoleBase):
    name = '猎人'
    team = '好人阵营'
    can_act_at_night = True  # 夜晚查看枪状态
    can_act_at_day = True  # 可在白天开枪（被投出时）
    needs_global_confirm = False

    def input_handlers(self):
        return {
            'hunter_confirm': self.confirm,
            'hunter_shoot_target': self.select_shoot_target,
            'hunter_shoot_confirm': self.confirm_shoot
        }

    def should_act(self) -> bool:
        room = self.user.room
        # 夜间被恐惧时无法行动
        if room.stage == GameStage.HUNTER and self.is_feared():
            return False
        return (self.user.status != PlayerStatus.DEAD and 
                room.stage == GameStage.HUNTER and 
                not self.user.skill.get('acted_this_stage', False))
        
    def get_actions(self) -> List:
        if self.in_shoot_mode():
            return self.get_shoot_actions()

        room = self.user.room
        if room and room.stage == GameStage.HUNTER and self.notify_fear_block():
            return []

        if not self.should_act():
            return []
        
        # 猎人睡眼时，发送开枪状态私聊消息 - 只发送一次
        if not self.user.skill.get('hunter_msg_sent', False):
            can_shoot = self.user.skill.get('can_shoot', True)
            status_msg = "可以开枪" if can_shoot else "不可以开枪"
            self.user.send_msg(f'🔫 你的开枪状态：{status_msg}')
            self.user.skill['hunter_msg_sent'] = True
        
        # 添加确认按键
        return [
            actions(
                name='hunter_confirm',
                buttons=['确认'],
                help_text='点击确认结束你的回合'
            )
        ]

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
        pending_choice = self.user.skill.get('hunter_pending_shot')
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
                name='hunter_shoot_target',
                buttons=buttons,
                help_text='请选择要带走的玩家'
            )
        ]
        if pending_choice:
            inputs.append(
                actions(
                    name='hunter_shoot_confirm',
                    buttons=[{'label': '确认击杀', 'value': 'confirm', 'color': 'danger'}],
                    help_text='确认执行击杀'
                )
            )
        return inputs

    @player_action
    def confirm(self) -> Optional[str]:
        # 猎人夜晚只是查看状态，标记为已行动即可
        self.user.skill['acted_this_stage'] = True
        # 清理消息发送标志
        self.user.skill.pop('hunter_msg_sent', None)
        return True

    def supports_last_skill(self) -> bool:
        return True

    def select_shoot_target(self, value: str):
        if not self.in_shoot_mode():
            return
        if value == 'cancel_shot':
            self.user.skill['pending_last_skill'] = False
            self.user.skill['last_words_skill_resolved'] = True
            self.user.skill['hunter_pending_shot'] = None
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
        self.user.skill['hunter_pending_shot'] = target.nick
        self.user.send_msg(f'已选择 {target_nick} 作为目标，点击确认击杀')

    def confirm_shoot(self, action: str):
        if not self.in_shoot_mode() or action != 'confirm':
            return
        target_nick = self.user.skill.pop('hunter_pending_shot', None)
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
