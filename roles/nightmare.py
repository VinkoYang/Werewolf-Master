# roles/nightmare.py
"""梦魇角色实现。

梦魇是狼队阵营的特殊角色，拥有【恐惧】技能：
- 先手于所有其他角色行动（单独睁眼）
- 恐惧目标当夜无法行动（技能无效化）
- 不可连续恐惧同一目标
- 不可恐惧自己
- 首夜恐惧狼队友时，狼队当夜空刀
- 非首夜不可恐惧狼队友
- 可被自爆或自刀
- 狼人阶段和其他狼人一起行动
"""
from typing import Optional, List
from pywebio.input import actions
from .wolf import Wolf
from .base import player_action
from enums import PlayerStatus, GameStage, Role

# 狼队角色（用于判断是否为狼队友）
WOLF_TEAM_ROLES = (Role.WOLF, Role.WOLF_KING, Role.WHITE_WOLF_KING, Role.NIGHTMARE)


class Nightmare(Wolf):
    """梦魇：继承狼人，额外拥有恐惧技能"""
    name = '梦魇'
    team = '狼人阵营'
    can_act_at_night = True
    needs_global_confirm = True  # 梦魇阶段需要确认

    def input_handlers(self):
        # 合并狼人的 handler 和梦魇自己的 handler
        handlers = super().input_handlers()
        handlers['nightmare_team_op'] = self.fear_player
        return handlers

    def should_act(self) -> bool:
        room = self.user.room
        if self.user.status == PlayerStatus.DEAD:
            return False
        
        # 梦魇阶段：使用恐惧技能
        if room.stage == GameStage.NIGHTMARE:
            return not self.user.skill.get('acted_this_stage', False)
        
        # 狼人阶段：使用狼人投票（调用父类）
        if room.stage == GameStage.WOLF:
            return super().should_act()
        
        return False

    def get_actions(self) -> List:
        room = self.user.room
        
        # 梦魇阶段：显示恐惧选择
        if room.stage == GameStage.NIGHTMARE and self.should_act():
            return self._get_fear_actions()
        
        # 狼人阶段：使用狼人的投票界面
        if room.stage == GameStage.WOLF:
            return super().get_actions()
        
        return []

    def _get_fear_actions(self) -> List:
        """获取恐惧技能的操作按钮"""
        room = self.user.room
        current_choice = self.user.skill.get('pending_fear')

        if not self.user.skill.get('nightmare_stage_ready', False):
            self.user.skill['nightmare_action_notified'] = False
            self.user.skill['nightmare_stage_ready'] = True

        buttons: List = []
        all_players = sorted(room.players.values(), key=lambda x: x.seat or 0)
        is_first_night = getattr(room, 'round', 1) == 1

        for u in all_players:
            label = f"{u.seat}. {u.nick}"
            btn = {'label': label, 'value': label}

            if u.status == PlayerStatus.DEAD:
                btn['disabled'] = True
                btn['color'] = 'secondary'
            elif u.nick == self.user.nick:
                # 不可恐惧自己
                btn['disabled'] = True
                btn['color'] = 'secondary'
                btn['label'] = f"{label}（不可恐惧自己）"
            elif self._is_consecutive_fear(u.nick):
                # 不可连续恐惧同一目标
                btn['disabled'] = True
                btn['color'] = 'secondary'
                btn['label'] = f"{label}（不可连恐）"
            elif not is_first_night and u.role in WOLF_TEAM_ROLES:
                # 非首夜不可恐惧狼队友
                btn['disabled'] = True
                btn['color'] = 'secondary'
                btn['label'] = f"{label}（狼队友）"
            elif u.nick == current_choice:
                btn['color'] = 'warning'
            elif is_first_night and u.role in WOLF_TEAM_ROLES:
                # 首夜可以恐惧狼队友（会导致狼队空刀），用特殊颜色标记
                btn['color'] = 'danger'

            buttons.append(btn)

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='nightmare_team_op',
                buttons=buttons,
                help_text='梦魇，请选择恐惧对象。被恐惧者当夜无法行动。'
            )
        ]

    def _is_consecutive_fear(self, target_nick: str) -> bool:
        """检查是否连续恐惧同一目标"""
        last_fear = self.user.skill.get('last_fear')
        last_fear_round = self.user.skill.get('last_fear_round')
        current_round = getattr(self.user.room, 'round', None)

        if last_fear is None or last_fear_round is None or current_round is None:
            return False

        # 如果上一轮恐惧了这个目标，则不可连续恐惧
        if last_fear == target_nick and last_fear_round == current_round - 1:
            return True

        return False

    @player_action
    def fear_player(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            return self.skip_fear(reason='manual')

        # 解析昵称：处理 "seat. nick" 格式
        target_nick = nick.split('.', 1)[-1].strip()

        # 验证目标
        if target_nick == self.user.nick:
            return '不能恐惧自己'

        if self._is_consecutive_fear(target_nick):
            return '不能连续两晚恐惧同一个玩家'

        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        room = self.user.room
        is_first_night = getattr(room, 'round', 1) == 1

        # 非首夜不可恐惧狼队友
        if not is_first_night and target.role in WOLF_TEAM_ROLES:
            return '非首夜不可恐惧狼队友'

        # 暂存恐惧目标，等待确认
        self.user.skill['pending_fear'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        room = self.user.room
        
        # 梦魇阶段：确认恐惧
        if room.stage == GameStage.NIGHTMARE:
            return self._confirm_fear()
        
        # 狼人阶段：使用狼人的确认逻辑
        if room.stage == GameStage.WOLF:
            return super().confirm()
        
        return None

    def _confirm_fear(self) -> Optional[str]:
        """确认恐惧技能"""
        nick = self.user.skill.pop('pending_fear', None)
        if nick is None:
            return '未选择目标'

        target = self.user.room.players.get(nick)
        if not target:
            return '查无此人'

        room = self.user.room
        is_first_night = getattr(room, 'round', 1) == 1

        # 标记目标被恐惧（使其当夜无法行动）
        target.skill['feared_this_night'] = True
        target.skill['feared_by'] = self.user.nick

        # 如果首夜恐惧狼队友，设置狼队空刀标记
        if is_first_night and target.role in WOLF_TEAM_ROLES:
            room.skill['wolf_forced_empty_knife'] = True
            self.user.send_msg(f'你恐惧了狼队友，今晚狼队将空刀。')
        else:
            self.user.send_msg(f'今晚，你恐惧了{target.seat}号玩家。')

        # 记录恐惧历史
        self.user.skill['last_fear'] = nick
        self.user.skill['last_fear_round'] = getattr(room, 'round', None)
        self.user.skill['acted_this_stage'] = True
        self.user.skill['nightmare_action_notified'] = True
        self.user.skill.pop('nightmare_stage_ready', None)

        return True

    def skip_fear(self, reason: Optional[str] = None):
        """放弃恐惧技能（内部方法，不使用 @player_action）"""
        if reason is None:
            reason = self.user.skill.get('skip_reason')

        # 如果已经行动过，不再处理
        if self.user.skill.get('acted_this_stage', False):
            return 'PENDING'

        # 只有明确的 manual 或 timeout 才发送消息并完成行动
        if reason in ('manual', 'timeout'):
            if not self.user.skill.get('nightmare_action_notified', False):
                self.user.send_msg('今晚，你没有恐惧任何人。')
                self.user.skill['nightmare_action_notified'] = True
            self.user.skill['acted_this_stage'] = True
            self.user.skill.pop('nightmare_stage_ready', None)
            self.user.room.waiting = False
            return True
        # 其他情况（刷新等）返回 PENDING，不结束等待
        return 'PENDING'

    @player_action
    def skip(self):
        """覆盖 Wolf 的 skip 方法，根据当前阶段选择正确的跳过逻辑"""
        room = self.user.room
        reason = self.user.skill.pop('skip_reason', None)
        # 梦魇阶段：只有在未行动且 reason 合法时才执行 skip_fear
        if room.stage == GameStage.NIGHTMARE:
            if not self.user.skill.get('acted_this_stage', False):
                # 只有 manual 或 timeout 才调用 skip_fear
                if reason in ('manual', 'timeout'):
                    return self.skip_fear(reason=reason)
                # 其他情况返回 PENDING，不结束等待
                return 'PENDING'
            return 'PENDING'
        # 狼人阶段使用父类的 skip 逻辑
        if room.stage == GameStage.WOLF:
            if reason is not None:
                self.user.skill['skip_reason'] = reason
            return super().skip()
        return 'PENDING'

    @classmethod
    def clear_fear_effects(cls, room):
        """清除所有玩家的恐惧状态（天亮时调用）"""
        for player in room.players.values():
            player.skill.pop('feared_this_night', None)
            player.skill.pop('feared_by', None)
            player.skill.pop('fear_notified', None)
        room.skill.pop('wolf_forced_empty_knife', None)
