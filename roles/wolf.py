# roles/wolf.py
from typing import List, Optional
from pywebio.input import actions
from .base import RoleBase, player_action
from enums import GameStage, PlayerStatus, Role

WOLF_ROLES = (Role.WOLF, Role.WOLF_KING, Role.WHITE_WOLF_KING, Role.NIGHTMARE)

class Wolf(RoleBase):
    name = '狼人'
    team = '狼人阵营'
    can_act_at_night = True
    needs_global_confirm = False

    def input_handlers(self):
        return {'wolf_team_op': self.kill_player}

    def should_act(self) -> bool:
        room = self.user.room
        # 狼人被恐惧时无法参与当晚行动
        if self.is_feared():
            return False
        return (
            self.user.status != PlayerStatus.DEAD and
            room.stage == GameStage.WOLF and
            not self.user.skill.get('wolf_action_done', False)
        )

    def get_actions(self) -> List:
        # 恐惧会直接阻断行动
        if self.notify_fear_block():
            return []

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
            elif self.user.nick in wolf_votes.get(u.nick, []):
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
            return self._abstain(reason='manual')

        # 解析传入的 "seat. nick" 格式，取出昵称
        target_nick = nick.split('.', 1)[-1].strip()
        result = self._apply_vote(target_nick)
        return result if result is not None else True

    @player_action
    def confirm(self) -> Optional[str]:
        target_nick = self.user.skill.get('wolf_choice', None)

        if not target_nick:
            return self._abstain(reason='manual')

        result = self._apply_vote(target_nick)
        return result if result is not None else True

    def _abstain(self, reason: Optional[str] = None):
        room = self.user.room
        if reason is None:
            reason = self.user.skill.get('skip_reason')

        # 只有明确的 manual/timeout 才真正执行放弃，防止刷新或重复回调乱入
        if reason not in ('manual', 'timeout'):
            return 'PENDING'
        # 如果已经行动过，不要重复发送消息
        if self.user.skill.get('wolf_action_done', False):
            return True
        
        # 先通知其他狼人该玩家放弃
        for u in room.players.values():
            if u.role in WOLF_ROLES and u.status == PlayerStatus.ALIVE:
                room.send_msg(f"{self.user.seat}号玩家选择放弃本夜击杀", nick=u.nick)
        
        # 再发送个人确认消息
        self.user.send_msg('你今夜放弃选择击杀目标')
        
        # 清理投票记录
        votes_map = room.skill.get('wolf_votes')
        if votes_map:
            for target, voters in list(votes_map.items()):
                if self.user.nick in voters:
                    voters.remove(self.user.nick)
                    if not voters:
                        votes_map.pop(target)
        self.user.skill.pop('wolf_choice', None)
        self.user.skill['wolf_action_done'] = True
        
        # 取消倒计时任务
        task = self.user.skill.pop('countdown_task', None)
        if task:
            task.cancel()
        
        self._check_all_wolves_acted()
        return True
    
    def _check_all_wolves_acted(self):
        """检查是否所有狼人都已行动，如果是则结束等待"""
        room = self.user.room
        if hasattr(room, 'get_active_wolves'):
            wolves = room.get_active_wolves()
        else:
            wolves = [
                u for u in room.players.values()
                if u.role in WOLF_ROLES and u.status == PlayerStatus.ALIVE
            ]
        all_acted = all(u.skill.get('wolf_action_done', False) for u in wolves)
        if all_acted:
            room.waiting = False

    @player_action
    def skip(self):
        reason = self.user.skill.pop('skip_reason', None)
        return self._abstain(reason=reason)

    def _apply_vote(self, target_nick: str) -> Optional[str]:
        room = self.user.room
        target_user = room.players.get(target_nick)
        if not target_user:
            return '查无此人'
        if target_user.status == PlayerStatus.DEAD:
            return '目标已死亡'

        votes_map = room.skill.setdefault('wolf_votes', {})
        # 移除此前的投票以避免重复计数
        for voted_target, voters in list(votes_map.items()):
            if self.user.nick in voters:
                voters.remove(self.user.nick)
                if not voters:
                    votes_map.pop(voted_target)

        votes_map.setdefault(target_nick, [])
        if self.user.nick not in votes_map[target_nick]:
            votes_map[target_nick].append(self.user.nick)

        self.user.skill['wolf_action_done'] = True
        self.user.skill.pop('wolf_choice', None)
        
        # 取消倒计时任务，防止超时后重复调用 skip
        task = self.user.skill.pop('countdown_task', None)
        if task:
            task.cancel()

        target_seat = target_user.seat if target_user else '?'
        for u in room.players.values():
            if u.role in WOLF_ROLES and u.status == PlayerStatus.ALIVE:
                room.send_msg(f"{self.user.seat}号玩家选择击杀{target_seat}号玩家", nick=u.nick)

        self._check_all_wolves_acted()
        return None
