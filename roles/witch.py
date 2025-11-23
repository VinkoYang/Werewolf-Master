# roles/witch.py
from typing import Optional, List
from pywebio.input import actions, radio
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, WitchRule, Role

class Witch(RoleBase):
    name = '女巫'
    team = '好人阵营'
    can_act_at_night = True
    needs_global_confirm = False

    def input_handlers(self):
        return {
            'witch_heal_confirm': self.heal_player,
            'witch_poison_op': self.select_poison_target,
            'witch_poison_confirm': self.confirm_poison
        }

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
        pending_targets = room.list_pending_kill_players()
        pending_seats = ', '.join(str(u.seat) for u in pending_targets) if pending_targets else ''
        inputs: List = []

        if not self.user.skill.get('witch_stage_ready', False):
            self.user.skill['witch_action_notified'] = False
            self.user.skill['witch_stage_ready'] = True

        # 解药：仅当有解药时才显示
        if self.has_heal():
            if pending_targets:
                msg = f"今夜{pending_seats}号玩家被杀，是否使用解药？"
                inputs.append(
                    actions(
                        name='witch_heal_confirm',
                        buttons=[{'label': '确认使用解药', 'value': 'confirm_heal', 'color': 'success'}],
                        help_text=msg
                    )
                )
            else:
                # 无人被杀的提示通过私聊发送一次
                if not self.user.skill.get('witch_no_kill_msg_sent', False):
                    self.user.send_msg('今夜无人被杀，无法使用解药')
                    self.user.skill['witch_no_kill_msg_sent'] = True

        # 毒药：显示所有玩家按钮
        if self.has_poison():
            current_choice = self.user.skill.get('pending_poison_target')
            buttons = []
            alive_players = sorted(room.players.values(), key=lambda x: x.seat or 0)
            for u in alive_players:
                label = f"{u.seat}. {u.nick}"
                btn = {'label': label, 'value': label}
                if u.nick == self.user.nick or u.status == PlayerStatus.DEAD:
                    btn['disabled'] = True
                    btn['color'] = 'secondary'
                elif u.nick == current_choice:
                    btn['color'] = 'danger'
                buttons.append(btn)

            buttons.append({'label': '不使用毒药', 'value': 'cancel_poison', 'color': 'secondary'})
            inputs.append(
                actions(
                    name='witch_poison_op',
                    buttons=buttons,
                    help_text='你有一瓶毒药，你要毒：'
                )
            )

            if current_choice:
                seat = room.players[current_choice].seat if current_choice in room.players else '?'
                inputs.append(
                    actions(
                        name='witch_poison_confirm',
                        buttons=[{'label': '确认', 'value': 'confirm_poison', 'color': 'danger'}],
                        help_text=f'确认对 {seat} 号玩家使用毒药？'
                    )
                )

        if not self.has_heal() and not self.has_poison():
            if not self.user.skill.get('witch_no_potion_sent', False):
                self.user.send_msg('你已经没有药了')
                self.user.skill['witch_no_potion_sent'] = True
            return []

        return inputs

    @player_action
    def heal_player(self, action: str) -> Optional[str]:
        """处理确认使用解药按钮"""
        if action != 'confirm_heal':
            return None
        
        room = self.user.room
        pending = room.list_pending_kill_players()
        
        if not pending:
            return '今夜无人被杀'
        
        if not self.has_heal():
            return '没有解药了'
        
        saved = []
        for target in pending:
            if room.witch_rule == WitchRule.NO_SELF_RESCUE and target.nick == self.user.nick:
                return '不能解救自己'
            if room.witch_rule == WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY:
                if target.nick == self.user.nick and room.round != 1:
                    return '仅第一晚可以解救自己'
            
            if target.status == PlayerStatus.PENDING_DEAD:
                target.status = PlayerStatus.PENDING_HEAL
                saved.append(target)
        
        self.user.skill['heal'] = False
        self.user.skill['acted_this_stage'] = True
        self.user.skill.pop('witch_heal_msg_sent', None)
        self.user.skill.pop('witch_no_kill_msg_sent', None)
        if saved:
            seats = ', '.join(str(t.seat) for t in saved)
            self.user.send_msg(f'今晚，你对{seats}号玩家使用解药')
        else:
            self.user.send_msg('今晚，你尝试使用解药，但无人需要')
        self.user.skill['witch_action_notified'] = True
        self.user.skill.pop('witch_stage_ready', None)
        return True
    
    @player_action
    def select_poison_target(self, nick: str) -> Optional[str]:
        """选择毒药目标"""
        if nick in ('不使用毒药', '取消', 'cancel_poison'):
            self.user.skill.pop('pending_poison_target', None)
            return 'PENDING'
        
        # 解析昵称
        target_nick = nick.split('.', 1)[-1].strip()
        
        if not self.has_poison():
            return '没有毒药了'
        
        target = self.user.room.players.get(target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        
        # 暂存毒人目标
        self.user.skill['pending_poison_target'] = target_nick
        return 'PENDING'
    
    @player_action
    def confirm_poison(self, action: str) -> Optional[str]:
        """确认使用毒药"""
        if action != 'confirm_poison':
            return None
        
        target_nick = self.user.skill.pop('pending_poison_target', None)
        if not target_nick:
            return '未选择目标'
        
        room = self.user.room
        target = room.players.get(target_nick)
        if not target:
            return '查无此人'
        
        if target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        
        target.status = PlayerStatus.PENDING_POISON
        if target.role == Role.HUNTER:
            target.skill['can_shoot'] = False
        self.user.skill['poison'] = False
        self.user.skill['acted_this_stage'] = True
        self.user.skill.pop('witch_heal_msg_sent', None)
        self.user.skill.pop('witch_no_kill_msg_sent', None)
        seat = target.seat if target else '?'
        self.user.send_msg(f'今晚，你对{seat}号玩家使用毒药')
        self.user.skill['witch_action_notified'] = True
        self.user.skill.pop('witch_stage_ready', None)
        return True

    @player_action
    def skip(self):
        if not self.user.skill.get('witch_action_notified', False):
            self.user.send_msg('今晚，你没有操作')
            self.user.skill['witch_action_notified'] = True
        self.user.skill.pop('witch_stage_ready', None)

