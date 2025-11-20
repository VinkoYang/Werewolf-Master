# roles/wolf.py
from typing import List, Optional
from pywebio.input import actions
from utils import add_cancel_button
from .base import RoleBase, player_action
from enums import GameStage, PlayerStatus

class Wolf(RoleBase):
    name = '狼人'
    team = '狼人阵营'
    can_act_at_night = True

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

        # 构建选择按钮：使用 dict 格式以支持 disabled/color
        buttons = []
        for u in players:
            label = f"{u.seat}. {u.nick}"
            disabled = (u.status == PlayerStatus.DEAD)
            btn = {'label': label, 'value': label}
            if disabled:
                btn['disabled'] = True
                btn['color'] = 'secondary'

            # 如果该玩家已被一个或多个狼人选择，标记为危险色
            voters = wolf_votes.get(u.nick, [])
            if voters:
                btn['color'] = 'danger'

            buttons.append(btn)

        # 在按钮上方显示当前被谁选择的状态
        summary_lines = []
        for target, voters in wolf_votes.items():
            summary_lines.append(f"{target} 被 {', '.join(voters)} 选择")

        inputs = []
        if summary_lines:
            from pywebio.output import put_html
            summary_html = "<br>".join(summary_lines)
            inputs.append(put_html(f"<div style='color:#c00'>{summary_html}</div>"))

        inputs.append(
            actions(
                name='wolf_team_op',
                buttons=buttons + [{'label': '放弃', 'type': 'cancel'}],
                help_text='狼人，请选择要击杀的对象。'
            )
        )

        return inputs

    @player_action
    def kill_player(self, nick: str) -> Optional[str]:
        if nick == '取消' or nick == '放弃':
            return None

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
        target_nick = self.user.skill.pop('wolf_choice', None)
        if not target_nick:
            return '未选择目标'
        votes_map = room.skill.setdefault('wolf_votes', {})
        votes_map.setdefault(target_nick, [])
        if self.user.nick not in votes_map[target_nick]:
            votes_map[target_nick].append(self.user.nick)
        self.user.skill['acted_this_stage'] = True
        return True
