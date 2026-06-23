"""觉醒隐狼（机械狼）- 狼队阵营，独立行动：先选择学习目标，后发动学习到的技能。

每晚只有一个机械狼阶段（位于狼人行动之前）：
  - 未学技能：MECHANICAL_WOLF_LEARN（学习）
  - 已学技能（学习于上一晚或更早）：MECHANICAL_WOLF_ACT（行动）

规则要点：
  - 不参与狼队讨论，不与其他狼人互知身份。
  - 学习狼人/狼王时，获得 mw_wolf_knife_ready 标志（跨夜保留）。
    额外狼刀仅在其他所有狼人（WOLF_TEAM_ROLES）全部出局后，
    才在 MECHANICAL_WOLF_ACT 阶段可用，且只能用一次。
    使用时为双刀：可选两个不同目标（默认）或同一目标（开启双刀破盾时）。
  - 学习守卫时称为机械盾；机械盾可挡狼刀和女巫毒，默认不挡猎人子弹。
    开启"机械盾抵挡猎人子弹"后，当天猎人出局对被盾玩家开枪无效。
  - 学习其他角色（魔镜少女/女巫/猎人）的技能，从下一晚起即可发动。
  - 学到猎人时，行动阶段可看到开枪状态；出局时若可开枪，可带走一名玩家。
"""
from typing import Optional, List

from stub import actions
from enums import PlayerStatus, GameStage, Role
from .base import RoleBase, player_action

_WOLF_ROLES = {Role.WOLF, Role.WOLF_KING}

_LEARN_LABELS = {
    Role.MAGIC_MIRROR_GIRL: ('魔镜少女', '每晚可查验一名玩家具体身份'),
    Role.SEER:              ('预言家',   '可查验一名玩家阵营'),
    Role.WITCH:             ('女巫',     '获得一瓶毒药'),
    Role.GUARD:             ('守卫',     '可守护一名玩家（机械盾）'),
    Role.HUNTER:            ('猎人',     '出局时可开枪带走一人'),
    Role.WOLF:              ('狼人',     '其余狼人出局后获得一次性双刀'),
    Role.WOLF_KING:         ('狼王',     '其余狼人出局后获得一次性双刀'),
    Role.CITIZEN:           ('平民',     '无主动技能'),
}


def _role_label(role: Role) -> str:
    if role in _LEARN_LABELS:
        name, desc = _LEARN_LABELS[role]
        return f'{name}（{desc}）'
    return role.value


class MechanicalWolf(RoleBase):
    name = '觉醒隐狼'
    team = '狼人阵营'
    can_act_at_night = True

    # ------------------------------------------------------------------ #
    # apparent role (shown to magic mirror girl after learning)
    # ------------------------------------------------------------------ #

    def get_apparent_role(self) -> Role:
        learned = self.user.skill.get('learned_role')
        if learned:
            return learned
        return Role.MECHANICAL_WOLF

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _all_other_wolves_dead(self) -> bool:
        """Returns True only when every player with a WOLF_TEAM_ROLES role is dead."""
        from presets.base import WOLF_TEAM_ROLES
        room = self.user.room
        return not any(
            u.status != PlayerStatus.DEAD and u.role in WOLF_TEAM_ROLES
            for u in room.players.values()
        )

    def _knife_available(self) -> bool:
        """Extra wolf knife: learned at some point, other wolves all dead, not yet used."""
        return (
            bool(self.user.skill.get('mw_wolf_knife_ready'))
            and not self.user.skill.get('mw_extra_knife_used')
            and self._all_other_wolves_dead()
        )

    # ------------------------------------------------------------------ #
    # Phase gates
    # ------------------------------------------------------------------ #

    def _in_learn_phase(self) -> bool:
        room = self.user.room
        return (
            self.user.status != PlayerStatus.DEAD
            and room.stage == GameStage.MECHANICAL_WOLF_LEARN
            and not self.user.skill.get('acted_this_stage', False)
        )

    def _in_act_phase(self) -> bool:
        room = self.user.room
        learned_night = self.user.skill.get('learned_night', room.round)
        return (
            self.user.status != PlayerStatus.DEAD
            and room.stage == GameStage.MECHANICAL_WOLF_ACT
            and not self.user.skill.get('acted_this_stage', False)
            and learned_night < room.round
        )

    def _in_shoot_mode(self) -> bool:
        """True when in LAST_WORDS phase, it's this player's turn, and hunter skill is ready."""
        room = self.user.room
        if not room or room.stage != GameStage.LAST_WORDS:
            return False
        day_state = getattr(room, 'day_state', {})
        if day_state.get('current_last_word') != self.user.nick:
            return False
        return (
            bool(self.user.skill.get('mw_hunter_ready'))
            and bool(self.user.skill.get('can_shoot'))
            and self.user.skill.get('pending_last_skill', False)
        )

    def should_act(self) -> bool:
        return self._in_learn_phase() or self._in_act_phase()

    def input_handlers(self):
        return {
            'mw_learn_op':      self.select_learn_target,
            'mw_knife_first':   self.select_first_knife_target,
            'mw_act_op':        self.select_act_target,
            'mw_shoot_target':  self.select_shoot_target,
            'mw_shoot_confirm': self.confirm_shoot,
        }

    def get_actions(self) -> List:
        if self._in_shoot_mode():
            return self._get_shoot_actions()
        if self._in_learn_phase():
            return self._get_learn_actions()
        if self._in_act_phase():
            return self._get_act_actions()
        return []

    # ------------------------------------------------------------------ #
    # Phase 1 — learn
    # ------------------------------------------------------------------ #

    def _get_learn_actions(self) -> List:
        room = self.user.room
        current_choice = self.user.skill.get('pending_learn')
        players = sorted(room.players.values(), key=lambda x: x.seat or 0)

        buttons = []
        for u in players:
            label = f"{u.seat}. {u.nick}"
            if u.nick == self.user.nick or u.status == PlayerStatus.DEAD:
                buttons.append({'label': label, 'value': label, 'disabled': True, 'color': 'secondary'})
            elif u.nick == current_choice:
                buttons.append({'label': label, 'value': label, 'color': 'warning'})
            else:
                buttons.append({'label': label, 'value': label})

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [actions(name='mw_learn_op', buttons=buttons, help_text='机械狼，请选择学习对象。')]

    @player_action
    def select_learn_target(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            self.user.skill.pop('pending_learn', None)
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃学习。')
            return True

        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        self.user.skill['pending_learn'] = target_nick
        return 'PENDING'

    @player_action
    def confirm(self) -> Optional[str]:
        room = self.user.room

        if room.stage == GameStage.MECHANICAL_WOLF_LEARN:
            target_nick = self.user.skill.pop('pending_learn', None)
            if not target_nick:
                self.user.skill['acted_this_stage'] = True
                self.user.send_msg('今夜，你放弃学习。')
                return True

            target = room.players.get(target_nick)
            if not target:
                return '查无此人'

            learned = target.role
            self.user.skill['learned_role'] = learned
            self.user.skill['learned_from'] = target_nick
            self.user.skill['learned_night'] = room.round

            # Persistent flags that survive re-learning
            if learned in _WOLF_ROLES:
                self.user.skill['mw_wolf_knife_ready'] = True
            if learned == Role.HUNTER:
                self.user.skill['mw_hunter_ready'] = True
                self.user.skill['can_shoot'] = True

            role_desc = _role_label(learned)
            if learned in _WOLF_ROLES:
                if self._all_other_wolves_dead():
                    extra = '（其余狼人已全部出局，双刀从下一晚起可用）'
                else:
                    extra = '（双刀将在其余狼人全部出局后，从下一晚起可用）'
                self.user.send_msg(f'你学习了{target.seat}号玩家的身份：{role_desc}{extra}')
            else:
                self.user.send_msg(f'你学习了{target.seat}号玩家的身份：{role_desc}，从下一晚起可发动技能。')

            self.user.skill['acted_this_stage'] = True
            return True

        if room.stage == GameStage.MECHANICAL_WOLF_ACT:
            return self._confirm_act()

        return None

    # ------------------------------------------------------------------ #
    # Phase 2 — act
    # ------------------------------------------------------------------ #

    def _get_act_actions(self) -> List:
        # Priority 1: double knife — only after all other wolves are dead
        if self._knife_available():
            return self._get_double_knife_actions()

        learned = self.user.skill.get('learned_role')
        if not learned:
            return self._act_acknowledge('今夜无可用技能。')

        if learned == Role.GUARD:
            return self._act_buttons('mw_act_op', '模仿守卫（机械盾）：选择守护目标', exclude_dead=True)

        if learned in (Role.MAGIC_MIRROR_GIRL, Role.SEER):
            return self._act_buttons('mw_act_op', '模仿魔镜少女：选择查验目标', exclude_dead=True,
                                     exclude_verified='mw_verified')

        if learned == Role.WITCH:
            if self.user.skill.get('mw_poison_used'):
                return self._act_acknowledge('毒药已使用，今夜无可用技能。')
            return self._act_buttons('mw_act_op', '模仿女巫：选择毒药目标', exclude_dead=True)

        if learned == Role.HUNTER:
            if self.user.skill.get('mw_hunter_notified_round') != self.user.room.round:
                can_shoot = self.user.skill.get('can_shoot', False)
                status = '可以开枪' if can_shoot else '不可开枪（已使用）'
                self.user.send_msg(f'猎人技能状态：{status}。出局时可选择带走一名玩家。')
                self.user.skill['mw_hunter_notified_round'] = self.user.room.round
            return self._act_acknowledge(None)

        if learned in _WOLF_ROLES:
            if self.user.skill.get('mw_wolf_notified_round') != self.user.room.round:
                if self.user.skill.get('mw_extra_knife_used'):
                    msg = '双刀已使用，今夜无可用技能。'
                else:
                    msg = '其余狼人尚未全部出局，双刀暂不可用。'
                self.user.send_msg(msg)
                self.user.skill['mw_wolf_notified_round'] = self.user.room.round
            return self._act_acknowledge(None)

        # Citizen or any unhandled case
        return self._act_acknowledge('今夜无可用技能。')

    def _get_double_knife_actions(self) -> List:
        """Double-knife UI: two-step target selection for the extra wolf knife."""
        room = self.user.room
        first_knife = self.user.skill.get('mw_first_knife')
        double_break = getattr(room, 'mw_double_knife_breaks_shield', False)

        if first_knife is None:
            # Step 1: select first knife target
            return self._act_buttons(
                'mw_knife_first',
                '双刀（第一刀）：选择第一个击杀目标',
                exclude_dead=True,
            )
        else:
            # Step 2: select second knife target
            first_player = room.players.get(first_knife)
            first_label = f"{first_player.seat}号" if first_player else first_knife
            current_choice = self.user.skill.get('pending_act_target')
            players = sorted(room.players.values(), key=lambda x: x.seat or 0)
            buttons = []
            for u in players:
                label = f"{u.seat}. {u.nick}"
                disabled = (
                    u.nick == self.user.nick
                    or u.status == PlayerStatus.DEAD
                    or (not double_break and u.nick == first_knife)
                )
                if disabled:
                    btn = {'label': label, 'value': label, 'disabled': True, 'color': 'secondary'}
                elif u.nick == first_knife:
                    btn = {'label': f"{label}（第一刀）", 'value': label, 'color': 'danger'}
                elif u.nick == current_choice:
                    btn = {'label': label, 'value': label, 'color': 'warning'}
                else:
                    btn = {'label': label, 'value': label}
                buttons.append(btn)
            buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
            return [actions(
                name='mw_act_op',
                buttons=buttons,
                help_text=f'机械狼（双刀第二刀）：第一刀已选{first_label}，请选择第二刀目标。',
            )]

    def _act_acknowledge(self, msg: Optional[str]) -> List:
        """Return a 知晓 button (mapped to '放弃') so the MW can complete the stage promptly."""
        if msg and self.user.skill.get('mw_ack_notified_round') != self.user.room.round:
            self.user.send_msg(msg)
            self.user.skill['mw_ack_notified_round'] = self.user.room.round
        return [actions(name='mw_act_op',
                        buttons=[{'label': '知晓', 'value': '放弃', 'color': 'secondary'}],
                        help_text='机械狼阶段（无需行动，点击知晓即可）。')]

    def _act_buttons(self, name: str, help_text: str, *,
                     exclude_dead: bool = False,
                     exclude_verified: Optional[str] = None) -> List:
        room = self.user.room
        current_choice = self.user.skill.get('pending_act_target')
        verified_key_set: set = self.user.skill.get(exclude_verified, set()) if exclude_verified else set()
        players = sorted(room.players.values(), key=lambda x: x.seat or 0)

        buttons = []
        for u in players:
            label = f"{u.seat}. {u.nick}"
            disabled = (
                u.nick == self.user.nick
                or (exclude_dead and u.status == PlayerStatus.DEAD)
                or u.nick in verified_key_set
            )
            if disabled:
                buttons.append({'label': label, 'value': label, 'disabled': True, 'color': 'secondary'})
            elif u.nick == current_choice:
                buttons.append({'label': label, 'value': label, 'color': 'warning'})
            else:
                buttons.append({'label': label, 'value': label})

        buttons.append({'label': '放弃', 'value': '放弃', 'color': 'secondary'})
        return [actions(name=name, buttons=buttons, help_text=f'机械狼（{help_text}）。')]

    @player_action
    def select_first_knife_target(self, nick: str) -> Optional[str]:
        """Handle first knife selection for the double-knife extra wolf knife."""
        if nick in ('取消', '放弃'):
            self.user.skill.pop('mw_first_knife', None)
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃双刀。')
            return True

        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'
        if target.status == PlayerStatus.DEAD:
            return '目标已出局'

        self.user.skill['mw_first_knife'] = target_nick
        seat = target.seat if target else '?'
        self.user.send_msg(f'双刀第一刀已选{seat}号玩家，请继续选择第二刀目标。')
        return 'PENDING'

    @player_action
    def select_act_target(self, nick: str) -> Optional[str]:
        if nick in ('取消', '放弃'):
            self.user.skill.pop('pending_act_target', None)
            # If double-knife first was set, abort the whole double knife
            self.user.skill.pop('mw_first_knife', None)
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃发动技能。')
            return True

        target_nick = nick.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target:
            return '查无此人'

        self.user.skill['pending_act_target'] = target_nick
        return 'PENDING'

    def _confirm_act(self) -> Optional[str]:
        # ── Double knife path ──────────────────────────────────────────
        if self._knife_available():
            return self._confirm_double_knife()

        target_nick = self.user.skill.pop('pending_act_target', None)
        if not target_nick:
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃发动技能。')
            return True

        room = self.user.room
        target = room.players.get(target_nick)
        if not target:
            return '查无此人'

        learned = self.user.skill.get('learned_role')

        if learned == Role.GUARD:
            if target.status not in (PlayerStatus.ALIVE, PlayerStatus.PENDING_DEAD, PlayerStatus.PENDING_HEAL):
                return '目标已出局'
            room.skill['mw_guarded_this_round'] = target_nick
            if target.status == PlayerStatus.PENDING_HEAL:
                # 奶穿：机械盾与女巫解药同时作用于同一玩家 → 出局
                target.status = PlayerStatus.PENDING_DEAD
                self.user.send_msg(f'你守护了{target.seat}号玩家（机械盾），但女巫解药已先生效，触发奶穿——该玩家将出局。')
            else:
                target.status = PlayerStatus.PENDING_GUARD
                self.user.send_msg(f'你守护了{target.seat}号玩家（机械盾）。')

        elif learned in (Role.MAGIC_MIRROR_GIRL, Role.SEER):
            role_inst = getattr(target, 'role_instance', None)
            if role_inst and hasattr(role_inst, 'get_apparent_role'):
                role_name = role_inst.get_apparent_role().value
            else:
                role_name = target.role.value if target.role else '未知'
            verified: set = self.user.skill.setdefault('mw_verified', set())
            verified.add(target_nick)
            self.user.send_msg(f'你查验了{target.seat}号玩家，他的身份是：{role_name}')

        elif learned == Role.WITCH:
            if self.user.skill.get('mw_poison_used'):
                self.user.send_msg('毒药已使用，今夜无可用技能。')
                self.user.skill['acted_this_stage'] = True
                return True
            if target.status == PlayerStatus.DEAD:
                return '目标已出局'
            self.user.skill['mw_poison_used'] = True
            target.status = PlayerStatus.PENDING_POISON
            self.user.send_msg(f'你对{target.seat}号玩家使用了毒药。')

        else:
            self.user.send_msg('该技能无需主动发动。')

        self.user.skill['acted_this_stage'] = True
        return True

    def _confirm_double_knife(self) -> Optional[str]:
        """Process the double-knife extra wolf knife action."""
        room = self.user.room
        first_nick = self.user.skill.pop('mw_first_knife', None)
        second_nick = self.user.skill.pop('pending_act_target', None)
        double_break = getattr(room, 'mw_double_knife_breaks_shield', False)

        # If nothing selected at all, forfeit
        if not first_nick and not second_nick:
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('今夜，你放弃双刀。')
            return True

        # If only first selected but not second, forfeit
        if first_nick and not second_nick:
            self.user.skill['acted_this_stage'] = True
            self.user.send_msg('未选择第二刀目标，今夜双刀放弃。')
            return True

        # Validate targets
        first_target = room.players.get(first_nick) if first_nick else None
        second_target = room.players.get(second_nick)

        if not second_target or second_target.status == PlayerStatus.DEAD:
            return '第二刀目标已出局'
        if first_target and first_target.status == PlayerStatus.DEAD:
            return '第一刀目标已出局'

        # In default mode, forbid same target
        if not double_break and first_nick == second_nick:
            self.user.send_msg('默认规则下双刀不可选同一目标，今夜双刀放弃。')
            self.user.skill['acted_this_stage'] = True
            return True

        self.user.skill['mw_extra_knife_used'] = True

        # Apply first knife
        if first_target and first_target.status == PlayerStatus.ALIVE:
            first_target.status = PlayerStatus.PENDING_DEAD

        # Apply second knife (same or different target)
        if first_nick == second_nick:
            # Breaking mode: same target — mark for guard bypass
            room.skill['mw_double_knife_target'] = second_nick
            self.user.send_msg(f'双刀破盾：你用双刀击杀了{second_target.seat}号玩家，守卫无法阻挡。')
        else:
            # Different targets — both die (guard can protect at most one)
            if second_target.status == PlayerStatus.ALIVE:
                second_target.status = PlayerStatus.PENDING_DEAD
            first_seat = first_target.seat if first_target else '?'
            self.user.send_msg(
                f'双刀：第一刀击中{first_seat}号玩家，第二刀击中{second_target.seat}号玩家。'
            )

        self.user.skill['acted_this_stage'] = True
        return True

    # ------------------------------------------------------------------ #
    # Phase 3 — hunter shoot (during LAST_WORDS when pending_last_skill)
    # ------------------------------------------------------------------ #

    def _get_shoot_actions(self) -> List:
        room = self.user.room
        buttons = []
        alive_players = sorted(room.list_alive_players(), key=lambda u: u.seat or 0)
        pending_choice = self.user.skill.get('mw_pending_shot')
        for player in alive_players:
            if player.nick == self.user.nick:
                continue
            label = f"{player.seat}. {player.nick}"
            btn: dict = {'label': label, 'value': label}
            if pending_choice == player.nick:
                btn['color'] = 'danger'
            buttons.append(btn)
        buttons.append({'label': '放弃开枪', 'value': 'cancel_shot', 'color': 'secondary'})
        inputs: List = [
            actions(name='mw_shoot_target', buttons=buttons, help_text='机械狼（猎人技能）：请选择要带走的玩家。')
        ]
        if pending_choice:
            inputs.append(
                actions(name='mw_shoot_confirm',
                        buttons=[{'label': '确认击杀', 'value': 'confirm', 'color': 'danger'}],
                        help_text='确认执行击杀')
            )
        return inputs

    def select_shoot_target(self, value: str):
        if not self._in_shoot_mode():
            return
        if value == 'cancel_shot':
            self.user.skill['pending_last_skill'] = False
            self.user.skill['last_words_skill_resolved'] = True
            self.user.skill.pop('mw_pending_shot', None)
            self.user.skill['can_shoot'] = False
            self.user.send_msg('你放弃了开枪。')
            return
        target_nick = value.split('.', 1)[-1].strip()
        target = self.user.room.players.get(target_nick)
        if not target or target.status != PlayerStatus.ALIVE:
            self.user.send_msg('目标不可用。')
            return
        if target.nick == self.user.nick:
            self.user.send_msg('不能击杀自己。')
            return
        self.user.skill['mw_pending_shot'] = target.nick
        self.user.send_msg(f'已选择 {target.seat}号 作为目标，点击确认击杀。')

    def confirm_shoot(self, action: str):
        if not self._in_shoot_mode() or action != 'confirm':
            return
        target_nick = self.user.skill.pop('mw_pending_shot', None)
        if not target_nick:
            self.user.send_msg('未选择目标。')
            return
        room = self.user.room
        target = room.players.get(target_nick)
        if not target or target.status != PlayerStatus.ALIVE:
            self.user.send_msg('目标不可用。')
            return

        # ── 机械盾抵挡猎人子弹 ──────────────────────────────────────────
        if (getattr(room, 'mw_shield_blocks_hunter', False)
                and room.skill.get('mw_guarded_this_round') == target_nick):
            seat = target.seat if target.seat is not None else '?'
            room.broadcast_msg(
                f'觉醒隐狼（猎人技能）开枪，但{seat}号玩家受机械盾保护，免疫此次击杀。', tts=True
            )
            self.user.skill['pending_last_skill'] = False
            self.user.skill['last_words_skill_resolved'] = True
            self.user.skill['can_shoot'] = False
            room.advance_last_words_progress(self.user)
            return

        seat = target.seat if target.seat is not None else '?'
        from_day_execution = (
            room.stage == GameStage.LAST_WORDS
            and bool(room.day_state.get('pending_execution'))
        )
        room.handle_last_word_skill_kill(target.nick, from_day_execution=from_day_execution)
        room.broadcast_msg(f'觉醒隐狼开枪，{seat}号玩家被带走。', tts=True)
        self.user.skill['pending_last_skill'] = False
        self.user.skill['last_words_skill_resolved'] = True
        self.user.skill['can_shoot'] = False
        room.advance_last_words_progress(self.user)

    # ------------------------------------------------------------------ #
    # Skip / timeout
    # ------------------------------------------------------------------ #

    @player_action
    def skip(self):
        self.user.skill.pop('pending_learn', None)
        self.user.skill.pop('mw_first_knife', None)
        self.user.skill.pop('pending_act_target', None)
        self.user.skill['acted_this_stage'] = True
        self.user.send_msg('今夜，你放弃行动。')
        if self.user.room:
            self.user.room.waiting = False

    # ------------------------------------------------------------------ #
    # Hunter-like passive: shoot when eliminated (if learned hunter)
    # ------------------------------------------------------------------ #

    def supports_last_skill(self) -> bool:
        return bool(self.user.skill.get('mw_hunter_ready') and self.user.skill.get('can_shoot'))
