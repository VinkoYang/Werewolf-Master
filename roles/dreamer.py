# roles/dreamer.py
from typing import List, Optional
from pywebio.input import actions
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Dreamer(RoleBase):
    name = '摄梦人'
    team = '好人阵营'
    can_act_at_night = True

    def input_handlers(self):
        return {'dreamer_team_op': self.select_target}

    def should_act(self) -> bool:
        room = self.user.room
        if self.is_feared():
            return False
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.DREAMER and not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        if self.notify_fear_block():
            return []

        if not self.should_act():
            return []
        
        room = self.user.room
        
        # 获取当前玩家的临时选择
        current_choice = self.user.skill.get('pending_dream_target')
        
        buttons = []
        players = sorted(
            room.players.values(),
            key=lambda x: x.seat if x.seat is not None else 0
        )
        for u in players:
            label = f"{u.seat}. {u.nick}"
            if u.nick == self.user.nick or u.status == PlayerStatus.DEAD:
                buttons.append({'label': label, 'value': label, 'disabled': True, 'color': 'secondary'})
            elif u.nick == current_choice:
                buttons.append({'label': label, 'value': label, 'color': 'warning'})
            else:
                buttons.append({'label': label, 'value': label})
        
        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='dreamer_team_op',
                buttons=buttons,
                help_text='摄梦人，请选择梦游对象。'
            )
        ]

    @player_action
    def select_target(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            self.user.skill.pop('pending_dream_target', None)
            self.user.skill['acted_this_stage'] = True
            self.user.skill['dream_skip_announced'] = True
            self.user.send_msg('今晚，你未指定梦游者。')
            return True
        # 解析 "seat. nick" 格式
        target_nick = nick.split('.', 1)[-1].strip()
        if target_nick == self.user.nick:
            return '不能选择自己'
        target = self.user.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'

        # 暂存梦游目标
        self.user.skill['pending_dream_target'] = target_nick
        seat = target.seat if target.seat is not None else '?'
        self.user.send_msg(f'今夜，你选择让{seat}号{target.nick}梦游。请确认。')
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        nick = self.user.skill.pop('pending_dream_target', None)
        if not nick:
            return '未选择目标'
        target = self.user.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        self.user.skill.pop('dream_skip_announced', None)
        self.user.skill['curr_dream_target'] = nick
        self.user.skill['acted_this_stage'] = True
        return True

    def apply_logic(self, room):
        prev_target = self.user.skill.get('last_dream_target')
        current_target = self.user.skill.pop('curr_dream_target', None)

        # 清除旧的梦游者链接
        for player in room.players.values():
            if player.skill.get('dreamer_nick') == self.user.nick:
                player.skill.pop('dreamer_nick', None)
                player.skill.pop('dream_forced_death', None)

        if not current_target:
            already_announced = self.user.skill.pop('dream_skip_announced', False)
            if not already_announced:
                self.user.send_msg('今晚，无人梦游。')
            self.user.skill['last_dream_target'] = None
            return

        target = room.players.get(current_target)
        if not target or target.status == PlayerStatus.DEAD:
            self.user.send_msg('目标已离场，今晚无人梦游。')
            self.user.skill['last_dream_target'] = None
            return

        target.skill['dream_immunity'] = True
        target.skill['dreamer_nick'] = self.user.nick

        seat = target.seat if target.seat is not None else '?'
        if prev_target == current_target:
            target.skill['dream_forced_death'] = 'streak'
            self.user.send_msg(f'连续两晚指定{seat}号{target.nick}梦游，他将被梦境吞噬。')
        else:
            target.skill.pop('dream_forced_death', None)
            self.user.send_msg(f'今晚，你指定{seat}号{target.nick}成为梦游者，免疫夜间伤害。')

        self.user.skill['last_dream_target'] = current_target
        self.user.skill.pop('dream_skip_announced', None)
