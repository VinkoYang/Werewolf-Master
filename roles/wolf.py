# roles/wolf.py
from typing import List, Optional
from pywebio.input import actions
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import GameStage, PlayerStatus, Role

class Wolf(RoleBase):
    name = '狼人'
    team = '狼人阵营'
    can_act_at_night = True

    def input_handlers(self):
        return {'wolf_team_op': self.kill_player}

    def should_act(self) -> bool:
        room = self.user.room
        return (
            self.user.status != PlayerStatus.DEAD and
            room.stage == GameStage.WOLF and
            not self.user.skill.get('acted_this_stage', False)
        )

    def get_actions(self) -> List:
        if not self.should_act():
            return []

        room = self.user.room
        # 显示所有玩家（包含自己），但已出局的玩家按钮不可用（灰色）
        players = sorted(room.players.values(), key=lambda x: x.seat if x.seat is not None else 0)

        # 收集当前的狼人投票信息，用于标记已被选择的目标
        wolf_votes = room.skill.get('wolf_votes', {})
        
        # 获取当前玩家的临时选择
        current_choice = self.user.skill.get('wolf_choice')

        # 构建选择按钮：使用 dict 格式以支持 disabled/color
        buttons = []
        for u in players:
            label = f"{u.seat}. {u.nick}"
            disabled = (u.status == PlayerStatus.DEAD)
            btn = {'label': label, 'value': label}
            if disabled:
                btn['disabled'] = True
                btn['color'] = 'secondary'
            # 如果是当前玩家的临时选择，标记为黄色（warning）
            elif u.nick == current_choice:
                btn['color'] = 'warning'
            # 如果该玩家已被其他狼人确认选择，标记为危险色
            elif u.nick in wolf_votes:
                btn['color'] = 'danger'

            buttons.append(btn)

        # 在按钮上方显示当前被谁选择的状态
        summary_lines = []
        for target, voters in wolf_votes.items():
            summary_lines.append(f"{target} 被 {', '.join(voters)} 选择")

        summary_text = '\n'.join(summary_lines)
        help_desc = '狼人，请选择要击杀的对象。'
        if summary_text:
            help_desc += f"\n当前选择：{summary_text}"

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='wolf_team_op',
                buttons=buttons,
                help_text=help_desc
            )
        ]

    @player_action
    def kill_player(self, nick: str) -> Optional[str]:
        if nick == '放弃':
            self._abstain()
            return 'PENDING'

        room = self.user.room

        # 解析传入的 "seat. nick" 格式，取出昵称
        target_nick = nick.split('.', 1)[-1].strip()

        # 只暂存选择，等待确认
        prev_choice = self.user.skill.get('wolf_choice')
        if prev_choice:
            votes_map = room.skill.setdefault('wolf_votes', {})
            if prev_choice in votes_map and self.user.nick in votes_map[prev_choice]:
                votes_map[prev_choice].remove(self.user.nick)

        self.user.skill['wolf_choice'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        # 将暂存的选择登记为正式投票
        room = self.user.room
        target_nick = self.user.skill.get('wolf_choice', None)
        
        # 如果玩家选择了"放弃"或没有选择
        if not target_nick:
            # 标记为已行动（放弃）
            self.user.skill['acted_this_stage'] = True
            # 广播给所有狼人：某玩家选择放弃
            for u in room.players.values():
                if u.role in (Role.WOLF, Role.WOLF_KING) and u.status == PlayerStatus.ALIVE:
                    room.send_msg(f"{self.user.seat}号玩家选择放弃", nick=u.nick)
            # 检查是否所有狼人都已行动
            self._check_all_wolves_acted()
            return 'PENDING'  # 不立即结束等待
        
        # 登记投票
        votes_map = room.skill.setdefault('wolf_votes', {})
        votes_map.setdefault(target_nick, [])
        if self.user.nick not in votes_map[target_nick]:
            votes_map[target_nick].append(self.user.nick)
        
        # 清除临时选择
        self.user.skill.pop('wolf_choice', None)
        self.user.skill['acted_this_stage'] = True
        
        # 广播给所有狼人：某玩家选择击杀某玩家
        target_user = room.players.get(target_nick)
        target_seat = target_user.seat if target_user else '?'
        for u in room.players.values():
            if u.role in (Role.WOLF, Role.WOLF_KING) and u.status == PlayerStatus.ALIVE:
                room.send_msg(f"{self.user.seat}号玩家选择击杀{target_seat}号玩家", nick=u.nick)
        
        # 检查是否所有狼人都已行动
        self._check_all_wolves_acted()
        return 'PENDING'  # 不立即结束等待，让其他狼人继续选择

    def _abstain(self):
        room = self.user.room
        votes_map = room.skill.get('wolf_votes')
        if votes_map:
            for target, voters in list(votes_map.items()):
                if self.user.nick in voters:
                    voters.remove(self.user.nick)
                    if not voters:
                        votes_map.pop(target)
        self.user.skill.pop('wolf_choice', None)
        self.user.skill['acted_this_stage'] = True
        self._check_all_wolves_acted()
    
    def _check_all_wolves_acted(self):
        """检查是否所有狼人都已行动，如果是则结束等待"""
        room = self.user.room
        wolves = [u for u in room.players.values() 
                 if u.role in (Role.WOLF, Role.WOLF_KING) and u.status == PlayerStatus.ALIVE]
        all_acted = all(u.skill.get('acted_this_stage', False) for u in wolves)
        if all_acted:
            room.waiting = False

    @player_action
    def skip(self):
        self._abstain()
        return 'PENDING'
