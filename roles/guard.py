# roles/guard.py
from typing import Optional, List
from pywebio.input import actions
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, GuardRule

class Guard(RoleBase):
    name = '守卫'
    team = '好人阵营'
    can_act_at_night = True

    def input_handlers(self):
        return {'guard_team_op': self.protect_player}

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.GUARD and not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        if not self.should_act():
            return []
        room = self.user.room
        current_choice = self.user.skill.get('pending_protect')

        if not self.user.skill.get('guard_stage_ready', False):
            self.user.skill['guard_action_notified'] = False
            self.user.skill['guard_stage_ready'] = True

        buttons: List = []
        all_players = sorted(room.players.values(), key=lambda x: x.seat or 0)
        for u in all_players:
            label = f"{u.seat}. {u.nick}"
            btn = {'label': label, 'value': label}
            if u.status == PlayerStatus.DEAD:
                btn['disabled'] = True
                btn['color'] = 'secondary'
            elif u.nick == current_choice:
                btn['color'] = 'warning'
            buttons.append(btn)

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='guard_team_op',
                buttons=buttons,
                help_text='守卫，请选择守护对象。'
            )
        ]

    @player_action
    def protect_player(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            return self.skip()
        
        # 解析昵称：处理 "seat. nick" 格式
        target_nick = nick.split('.', 1)[-1].strip()
        
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        # 暂存守护目标，等待确认
        self.user.skill['pending_protect'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        nick = self.user.skill.pop('pending_protect', None)
        if nick is None:
            return '未选择目标'
        target = self.user.room.players.get(nick)
        if not target:
            return '查无此人'
        protected_from_poison = target.status == PlayerStatus.PENDING_POISON
        if not protected_from_poison:
            if target.status == PlayerStatus.PENDING_HEAL and self.user.room.guard_rule == GuardRule.MED_CONFLICT:
                target.status = PlayerStatus.PENDING_DEAD
            else:
                target.status = PlayerStatus.PENDING_GUARD
        self.user.skill['last_protect'] = nick
        self.user.skill['acted_this_stage'] = True
        seat = target.seat if target else '?'
        self.user.send_msg(f'今晚，你守护了{seat}号玩家')
        self.user.skill['guard_action_notified'] = True
        self.user.skill.pop('guard_stage_ready', None)
        return True

    @player_action
    def skip(self):
        if not self.user.skill.get('guard_action_notified', False):
            self.user.send_msg('今晚，你没有操作')
            self.user.skill['guard_action_notified'] = True
        self.user.skill.pop('guard_stage_ready', None)
        self.user.skill['acted_this_stage'] = True
        if self.user.room:
            self.user.room.waiting = False
