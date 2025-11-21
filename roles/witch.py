# roles/witch.py
from typing import Optional, List
from pywebio.input import actions, radio
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, WitchRule

class Witch(RoleBase):
    name = '女巫'
    team = '好人阵营'
    can_act_at_night = True

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.WITCH and not self.user.skill.get('acted_this_stage', False)

    def has_heal(self) -> bool:
        return self.user.skill.get('heal', False)

    def has_poison(self) -> bool:
        return self.user.skill.get('poison', False)

    def get_actions(self) -> List:
        if not self.should_act():
            return []

        room = self.user.room
        pending = room.list_pending_kill_players()
        pending_nicks = ', '.join([u.nick for u in pending]) if pending else None

        # 提示信息
        if pending_nicks:
            self.user.send_msg(f'今夜被杀的是 {pending_nicks}')
        else:
            self.user.send_msg('今夜无人被杀')

        # 构建选项
        heal_btn = self.has_heal()
        poison_btn = self.has_poison()

        mode_options = []
        if heal_btn:
            mode_options.append('解药')
        if poison_btn:
            mode_options.append('毒药')
        if not mode_options:
            self.user.send_msg('你已经没有药了')
            return []

        # 获取当前玩家的临时选择
        pending_action = self.user.skill.get('pending_witch_action')
        current_choice = pending_action[1] if pending_action else None
        
        # 构建按钮列表，添加黄色标记
        buttons = []
        for u in room.list_alive_players():
            label = f"{u.seat}. {u.nick}"
            # 如果是当前玩家的临时选择，标记为黄色（warning）
            if u.nick == current_choice:
                buttons.append({'label': label, 'value': label, 'color': 'warning'})
            else:
                buttons.append({'label': label, 'value': label})
        
        buttons.append({'label': '取消', 'type': 'cancel'})

        # 选择操作后需要确认
        return [
            radio(name='witch_mode', options=mode_options, required=True, inline=True),
            actions(
                name='witch_team_op',
                buttons=buttons,
                help_text='女巫，请选择你的操作。'
            )
        ]

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

        # 只有 PENDING_DEAD 才能救
        if target.status != PlayerStatus.PENDING_DEAD:
            return '此人未被刀'

        # 暂存救人选择，等待确认
        self.user.skill['pending_witch_action'] = ('heal', nick)
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        pending = self.user.skill.pop('pending_witch_action', None)
        if not pending:
            return '未选择操作'
        mode, nick = pending
        target = room.players.get(nick)
        if not target:
            return '查无此人'
        if mode == 'heal':
            if target.status != PlayerStatus.PENDING_DEAD:
                return '此人未被刀'
            target.status = PlayerStatus.PENDING_HEAL
            self.user.skill['heal'] = False
            self.user.skill['acted_this_stage'] = True
            return True
        elif mode == 'kill':
            if target.status == PlayerStatus.DEAD:
                return '目标已死亡'
            target.status = PlayerStatus.PENDING_POISON
            self.user.skill['poison'] = False
            self.user.skill['acted_this_stage'] = True
            return True

    @player_action
    def kill_player(self, nick: str) -> Optional[str]:
        if nick == '取消':
            return None
        if not self.has_poison():
            return '没有毒药了'
        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        # 暂存毒人选择，等待确认
        self.user.skill['pending_witch_action'] = ('kill', target_nick)
        return 'PENDING'
