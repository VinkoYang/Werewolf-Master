# roles/wolf_beauty.py
"""狼美人角色实现。

狼美人是狼队阵营的特殊角色，拥有【魅惑】技能：
- 每晚可魅惑一名玩家
- 狼美人因毒/梦/放逐/被射杀出局时，被魅惑者殉情一并出局且无技能
- 不可自爆或自刀
- 被魅惑者夜间不知情
- 殉情者不能发动技能（猎人闷枪、狼王不能开爪）
"""
from typing import Optional, List
from pywebio.input import actions
from .wolf import Wolf
from .base import player_action
from enums import PlayerStatus, GameStage, Role

# 狼队角色
WOLF_TEAM_ROLES = (Role.WOLF, Role.WOLF_KING, Role.WHITE_WOLF_KING, Role.NIGHTMARE, Role.WOLF_BEAUTY)


class WolfBeauty(Wolf):
    """狼美人：继承狼人，额外拥有魅惑技能"""
    name = '狼美人'
    team = '狼人阵营'
    can_act_at_night = True
    needs_global_confirm = True  # 狼美人阶段需要确认

    def input_handlers(self):
        # 合并狼人的 handler 和狼美人自己的 handler
        handlers = super().input_handlers()
        handlers['wolf_beauty_op'] = self.charm_player
        return handlers

    def should_act(self) -> bool:
        room = self.user.room
        if self.user.status == PlayerStatus.DEAD:
            return False
        
        # 狼美人阶段：使用魅惑技能
        if room.stage == GameStage.WOLF_BEAUTY:
            return not self.user.skill.get('acted_this_stage', False)
        
        # 狼人阶段：使用狼人投票（调用父类）
        if room.stage == GameStage.WOLF:
            return super().should_act()
        
        return False

    def get_actions(self) -> List:
        room = self.user.room
        
        # 狼美人阶段：显示魅惑选择
        if room.stage == GameStage.WOLF_BEAUTY and self.should_act():
            return self._get_charm_actions()
        
        # 狼人阶段：使用狼人的投票界面
        if room.stage == GameStage.WOLF:
            return super().get_actions()
        
        return []

    def _get_charm_actions(self) -> List:
        """获取魅惑技能的操作按钮"""
        room = self.user.room
        current_choice = self.user.skill.get('pending_charm')

        if not self.user.skill.get('wolf_beauty_stage_ready', False):
            self.user.skill['wolf_beauty_action_notified'] = False
            self.user.skill['wolf_beauty_stage_ready'] = True
            # 确保进入该阶段时可以行动（防止上一阶段遗留的 acted_this_stage 状态）
            self.user.skill['acted_this_stage'] = False

        buttons: List = []
        all_players = sorted(room.players.values(), key=lambda x: x.seat or 0)

        last_target = self.user.skill.get('charm_target')
        last_round = self.user.skill.get('charm_round')
        current_round = room.round

        for u in all_players:
            label = f"{u.seat}. {u.nick}"
            btn = {'label': label, 'value': label, 'color': 'primary'}

            if u.status == PlayerStatus.DEAD:
                btn['disabled'] = True
                btn['color'] = 'secondary'
            elif u.nick == self.user.nick:
                # 不可魅惑自己
                btn['disabled'] = True
                btn['color'] = 'secondary'
                btn['label'] = f"{label}（不可魅惑自己）"
            elif (
                last_target == u.nick and
                isinstance(last_round, int) and
                last_round == current_round - 1
            ):
                btn['disabled'] = True
                btn['color'] = 'secondary'
                btn['label'] = f"{label}（不可连续两晚魅惑）"
            elif u.nick == current_choice:
                btn['color'] = 'warning'

            buttons.append(btn)

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [
            actions(
                name='wolf_beauty_op',
                buttons=buttons,
                help_text='狼美人，请选择魅惑对象。若你出局，被魅惑者将殉情。'
            )
        ]

    @player_action
    def charm_player(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            return self._skip_charm(reason='manual')

        # 解析昵称：处理 "seat. nick" 格式
        target_nick = nick.split('.', 1)[-1].strip()

        # 验证目标
        if target_nick == self.user.nick:
            return '不能魅惑自己'

        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        if target.status == PlayerStatus.DEAD:
            return '目标已出局'

        # 暂存魅惑目标，等待确认
        self.user.skill['pending_charm'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        room = self.user.room
        
        # 狼美人阶段：确认魅惑
        if room.stage == GameStage.WOLF_BEAUTY:
            return self._confirm_charm()
        
        # 狼人阶段：使用狼人的确认逻辑
        if room.stage == GameStage.WOLF:
            return super().confirm()
        
        return None

    def _confirm_charm(self) -> Optional[str]:
        """确认魅惑技能"""
        nick = self.user.skill.pop('pending_charm', None)
        if nick is None:
            return '未选择目标'

        room = self.user.room
        target = room.players.get(nick)
        if not target:
            return '查无此人'

        last_round = self.user.skill.get('charm_round')
        last_target = self.user.skill.get('charm_target')
        if last_target == nick and isinstance(last_round, int) and last_round == room.round - 1:
            return '不可连续两晚魅惑同一玩家'

        if last_target and last_target != nick:
            prev_target = room.players.get(last_target)
            if prev_target:
                prev_target.skill.pop('charmed_by', None)

        # 标记目标被魅惑
        target.skill['charmed_by'] = self.user.nick
        self.user.skill['charm_target'] = nick
        self.user.skill['charm_round'] = room.round

        self.user.send_msg(f'今晚，你魅惑了{target.seat}号玩家。')

        self.user.skill['acted_this_stage'] = True
        self.user.skill['wolf_beauty_action_notified'] = True
        self.user.skill.pop('wolf_beauty_stage_ready', None)

        return True

    def _skip_charm(self, reason: Optional[str] = None):
        """放弃魅惑技能"""
        if reason is None:
            reason = self.user.skill.get('skip_reason')

        # 如果已经行动过，不再处理
        if self.user.skill.get('acted_this_stage', False):
            return 'PENDING'

        # 只有明确的 manual 或 timeout 才发送消息并完成行动
        if reason in ('manual', 'timeout'):
            if not self.user.skill.get('wolf_beauty_action_notified', False):
                self.user.send_msg('今晚，你没有魅惑任何人。')
                self.user.skill['wolf_beauty_action_notified'] = True
            self.user.skill['acted_this_stage'] = True
            self.user.skill.pop('wolf_beauty_stage_ready', None)
            self.user.room.waiting = False
            return True
        # 其他情况（刷新等）返回 PENDING，不结束等待
        return 'PENDING'

    @player_action
    def skip(self):
        """覆盖 Wolf 的 skip 方法，根据当前阶段选择正确的跳过逻辑"""
        room = self.user.room
        reason = self.user.skill.pop('skip_reason', None)
        # 狼美人阶段：只有在未行动且 reason 合法时才执行 skip
        if room.stage == GameStage.WOLF_BEAUTY:
            if not self.user.skill.get('acted_this_stage', False):
                if reason in ('manual', 'timeout'):
                    return self._skip_charm(reason=reason)
                return 'PENDING'
            return 'PENDING'
        # 狼人阶段使用父类的 skip 逻辑
        if room.stage == GameStage.WOLF:
            if reason is not None:
                self.user.skill['skip_reason'] = reason
            return super().skip()
        return 'PENDING'

    @classmethod
    def handle_wolf_beauty_death(cls, room, wolf_beauty_user):
        """处理狼美人出局时的殉情逻辑"""
        charm_target_nick = wolf_beauty_user.skill.get('charm_target')
        if not charm_target_nick:
            return None

        target = room.players.get(charm_target_nick)
        if not target or target.status == PlayerStatus.DEAD:
            return None

        # 被魅惑者殉情
        target.status = PlayerStatus.DEAD
        # 殉情者不能发动技能
        target.skill['can_shoot'] = False
        target.skill['charmed_death'] = True

        seat = target.seat if target.seat is not None else '?'
        room.broadcast_msg(f'{seat}号玩家被狼美人魅惑，殉情出局。')

        return target.nick

    @classmethod
    def clear_charm_effects(cls, room):
        """清除所有玩家的魅惑状态（新的一晚开始时调用）"""
        for player in room.players.values():
            player.skill.pop('charmed_by', None)
        # 注意：charm_target 保留，因为殉情效果持续整局
