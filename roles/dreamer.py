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
        
        # 获取当前玩家的临时选择
        current_choice = self.user.skill.get('pending_dream_target')
        
        buttons = []
        for u in room.list_alive_players():
            if u.nick != self.user.nick:
                label = f"{u.seat}. {u.nick}"
                # 如果是当前玩家的临时选择，标记为黄色（warning）
                if u.nick == current_choice:
                    buttons.append({'label': label, 'value': label, 'color': 'warning'})
                else:
                    buttons.append({'label': label, 'value': label})
        
        buttons.append({'label': '取消', 'type': 'cancel'})
        return [
            actions(
                name='dreamer_team_op',
                buttons=buttons,
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

        # 暂存梦游目标
        self.user.skill['pending_dream_target'] = nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        nick = self.user.skill.pop('pending_dream_target', None)
        if not nick:
            return '未选择目标'
        target = self.user.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        self.user.skill['curr_dream_target'] = nick
        self.user.skill['acted_this_stage'] = True
        return True

    # apply_logic 保持不变
