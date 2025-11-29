# roles/seer.py
from typing import Optional, List
from pywebio.input import actions
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage, Role

class Seer(RoleBase):
    name = '预言家'
    team = '好人阵营'
    can_act_at_night = True

    def input_handlers(self):
        return {'seer_team_op': self.identify_player}

    def should_act(self) -> bool:
        room = self.user.room
        if self.is_feared():
            return False
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.SEER and not self.user.skill.get('acted_this_stage', False)

    def get_actions(self) -> List:
        room = self.user.room
        if not room or room.stage != GameStage.SEER:
            return []

        if self.notify_fear_block():
            return []

        if not self.should_act():
            return []
        
        # 显示所有玩家（包括自己和已出局的），自己和已出局的按钮灰色且不可选
        players = sorted(room.players.values(), key=lambda x: x.seat if x.seat is not None else 0)
        
        # 获取当前玩家的临时选择
        current_choice = self.user.skill.get('pending_target')
        
        buttons = []
        for u in players:
            label = f"{u.seat}. {u.nick}"
            # 自己或已出局的玩家：灰色且禁用
            if u.nick == self.user.nick or u.status == PlayerStatus.DEAD:
                buttons.append({'label': label, 'value': label, 'disabled': True, 'color': 'secondary'})
            # 如果是当前玩家的临时选择，标记为黄色（warning）
            elif u.nick == current_choice:
                buttons.append({'label': label, 'value': label, 'color': 'warning'})
            else:
                buttons.append({'label': label, 'value': label})
        
        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='seer_team_op',
                buttons=buttons,
                help_text='预言家，请查验身份。'
            )
        ]

    @player_action
    def identify_player(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            self.user.skill.pop('pending_target', None)
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃查验。')
            return True
        
        # 解析昵称：处理 "seat. nick" 格式
        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'
        # 暂存选择，等待确认
        self.user.skill['pending_target'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        target_nick = self.user.skill.pop('pending_target', None)
        if not target_nick:
            return '未选择目标'
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'
        
        # 判断目标阵营：狼人或好人
        wolf_team_roles = {
            Role.WOLF,
            Role.WOLF_KING,
            Role.WHITE_WOLF_KING,
            Role.WOLF_BEAUTY,
            Role.NIGHTMARE,
        }
        if target.role == Role.HALF_BLOOD:
            camp = '好人'
        elif target.role in wolf_team_roles:
            camp = '狼人'
        else:
            camp = '好人'
        
        # 发送私聊消息，显示座位号和阵营
        self.user.send_msg(f'你选择查验{target.seat}号玩家，他的身份是{camp}')
        self.user.skill['acted_this_stage'] = True
        return True
