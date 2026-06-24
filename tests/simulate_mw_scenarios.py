"""
四个指定场景模拟（机械狼-镜隐迷踪 版型）

场景A：机械狼第1晚学习通灵师，通灵师第1晚查验机械狼
  → 通灵师查验结果：通灵师（被伪装欺骗！）

场景B：机械狼第1晚学习女巫，通灵师第1晚查验机械狼
  → 通灵师查验结果：女巫（机械狼伪装成女巫）

场景C：机械狼第1晚学习守卫（机械盾），通灵师第1晚查验机械狼
  → 通灵师查验结果：守卫（机械狼伪装成守卫）

场景D：机械狼第1晚学习猎人，通灵师第1晚查验机械狼，第2晚狼刀机械狼
  → 通灵师查验结果：猎人；机械狼被刀后触发猎人技能开枪

用法（从项目根目录运行）：
    python -m tests.simulate_mw_scenarios
"""

import asyncio
import random
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import utils as _utils_mod

async def _fast_sleep(seconds: float):
    await asyncio.sleep(min(seconds, 0.01))

from presets.base import DefaultGameFlow as _DGF
_original_wait_for_player = _DGF.wait_for_player

async def _fast_wait_for_player(self, *, min_duration=None, auto_release=False, silent_timeout=False):
    return await _original_wait_for_player(
        self, min_duration=0, auto_release=auto_release, silent_timeout=silent_timeout
    )

from models.system import Global
from models.user import User
from models.room import Room
from models.lobby import resolve_room_config, build_roles_from_config
from enums import PlayerStatus, GameStage, Role
from presets.base import WOLF_CAMP_ROLES

import presets.base as _presets_base_mod
import models.room as _models_room_mod
import models.runtime.tools as _models_runtime_tools_mod
import models.runtime.sheriff as _models_runtime_sheriff_mod

_ASYNC_SLEEP_MODULES = [
    _utils_mod,
    _presets_base_mod,
    _models_room_mod,
    _models_runtime_tools_mod,
    _models_runtime_sheriff_mod,
]
_original_async_sleep = _utils_mod.async_sleep


def make_users(n: int) -> list:
    Global.users.clear()
    Global.rooms.clear()
    users = []
    for i in range(n):
        nick = f'bot{i+1}'
        user = User(nick=nick, sid=f'fake_{i}', reconnect_token=f'tok{i}')
        Global.users[nick] = user
        users.append(user)
    return users


def setup_room(preset: str, users: list) -> Room:
    config = resolve_room_config(preset)
    needed = len(build_roles_from_config(config))
    room = Room.alloc(config)
    for user in users[:needed]:
        room.add_player(user)
    return room


def print_log(room: Room, since: int, prefix: str = '') -> int:
    for sender, content in room.log[since:]:
        text = content.get('text', '') if isinstance(content, dict) else str(content)
        tag = f'[{sender}]' if sender else '[SYS]'
        if text:
            print(f'{prefix}{tag} {text}')
    return len(room.log)


def print_roles(room: Room):
    print('\n--- 玩家角色 ---')
    for user in room.players.values():
        role = user.role.value if user.role else '?'
        status = '' if user.status == PlayerStatus.ALIVE else ' ✝'
        print(f'  {user.seat:2}. {user.nick}: {role}{status}')
    print()


def find_by_role(room: Room, role: Role):
    """返回存活的指定角色玩家，找不到则返回 None。"""
    return next(
        (u for u in room.players.values()
         if u.role == role and u.status != PlayerStatus.DEAD),
        None
    )


class ScenarioWatcher:
    """
    可配置的场景驱动器：
    - forced_mw_learn_role: 机械狼第1晚强制学习的角色
    - force_wolves_kill_mw_night: 该夜强制所有存活狼人击杀机械狼（模拟狼刀机械狼）
    - mmg_checks_mw_night: 该夜通灵师强制查验机械狼（默认第1晚）
    """

    def __init__(self, room: Room, forced_mw_learn_role: Role, *,
                 force_wolves_kill_mw_night: int = None,
                 mmg_checks_mw_night: int = 1):
        self.room = room
        self.forced_mw_learn_role = forced_mw_learn_role
        self.force_wolves_kill_mw_night = force_wolves_kill_mw_night
        self.mmg_checks_mw_night = mmg_checks_mw_night
        self._seer_checked: set = set()

    def alive_players(self) -> list:
        return [u for u in self.room.players.values() if u.status == PlayerStatus.ALIVE]

    def acting_players(self) -> list:
        return [u for u in self.room.players.values()
                if u.status in {PlayerStatus.ALIVE, PlayerStatus.PENDING_DEAD}]

    def wolves(self) -> list:
        return [u for u in self.alive_players() if u.role in WOLF_CAMP_ROLES]

    def non_wolves(self) -> list:
        return [u for u in self.alive_players() if u.role not in WOLF_CAMP_ROLES]

    def sheriff(self):
        captain = self.room.skill.get('sheriff_captain')
        if captain and captain in self.room.players:
            return self.room.players[captain]
        return None

    # ── Night handlers ────────────────────────────────────────────────────

    def _handle_waiting(self):
        stage = self.room.stage
        if stage == GameStage.WOLF:
            return self._act_wolves()
        if stage == GameStage.SEER:
            return self._act_seer()
        if stage == GameStage.WITCH:
            return self._act_witch()
        if stage == GameStage.GUARD:
            return self._act_guard()
        if stage == GameStage.HUNTER:
            return self._act_hunter_night()
        if stage == GameStage.MECHANICAL_WOLF_LEARN:
            return self._act_mechanical_wolf_learn()
        if stage == GameStage.MECHANICAL_WOLF_ACT:
            return self._act_mechanical_wolf_act()
        if stage == GameStage.MAGIC_MIRROR_GIRL:
            return self._act_magic_mirror_girl()
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                user.skip(reason='timeout')
                return True
        self.room.waiting = False
        return True

    def _act_wolves(self):
        # 特定夜晚强制狼人击杀机械狼（场景D：模拟狼队误刀机械狼）
        if (self.force_wolves_kill_mw_night
                and self.room.round == self.force_wolves_kill_mw_night):
            mw = find_by_role(self.room, Role.MECHANICAL_WOLF)
            if mw:
                for user in self.alive_players():
                    ri = user.role_instance
                    if ri and ri.should_act():
                        ri.kill_player(f"{mw.seat}. {mw.nick}")
                return True

        targets = self.non_wolves()
        for user in self.alive_players():
            ri = user.role_instance
            if ri and ri.should_act():
                if targets:
                    target = random.choice(targets)
                    ri.kill_player(f"{target.seat}. {target.nick}")
                else:
                    user.skip(reason='timeout')
        return True

    def _act_seer(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                candidates = [u for u in self.alive_players()
                              if u.nick != user.nick and u.nick not in self._seer_checked]
                if candidates:
                    target = random.choice(candidates)
                    self._seer_checked.add(target.nick)
                    rv = ri.identify_player(f"{target.seat}. {target.nick}")
                    if rv == 'PENDING':
                        ri.confirm()
                else:
                    user.skip(reason='timeout')
                return True
        self.room.waiting = False
        return True

    def _act_witch(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                pending_dead = [p for p in self.room.players.values()
                                if p.status == PlayerStatus.PENDING_DEAD]
                effective_dead = [p for p in pending_dead if not ri._self_rescue_blocked(p)]
                if effective_dead and ri.has_heal():
                    ri.heal_player('confirm_heal')
                elif ri.has_poison():
                    poison_targets = [u for u in self.alive_players() if u.nick != user.nick]
                    if poison_targets:
                        target = random.choice(poison_targets)
                        rv = ri.select_poison_target(f"{target.seat}. {target.nick}")
                        if rv == 'PENDING':
                            ri.confirm_poison('confirm_poison')
                    else:
                        user.skip(reason='timeout')
                else:
                    user.skip(reason='timeout')
                return True
        self.room.waiting = False
        return True

    def _act_guard(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                last_protected = user.skill.get('last_protect')
                candidates = [u for u in self.alive_players() if u.nick != last_protected]
                if candidates:
                    target = random.choice(candidates)
                    rv = ri.protect_player(f"{target.seat}. {target.nick}")
                    if rv == 'PENDING':
                        ri.confirm()
                else:
                    user.skip(reason='timeout')
                return True
        self.room.waiting = False
        return True

    def _act_hunter_night(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                ri.confirm()
                return True
        self.room.waiting = False
        return True

    def _act_mechanical_wolf_learn(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                # 第1晚强制学习指定角色，后续随机
                if self.room.round == 1:
                    forced = find_by_role(self.room, self.forced_mw_learn_role)
                    target = forced if forced and forced.nick != user.nick else None
                    if not target:
                        candidates = [u for u in self.alive_players() if u.nick != user.nick]
                        target = random.choice(candidates) if candidates else None
                else:
                    candidates = [u for u in self.alive_players() if u.nick != user.nick]
                    target = random.choice(candidates) if candidates else None

                if target:
                    rv = ri.select_learn_target(f"{target.seat}. {target.nick}")
                    if rv == 'PENDING':
                        ri.confirm()
                else:
                    ri.skip()
                return True
        self.room.waiting = False
        return True

    def _act_mechanical_wolf_act(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                available = ri.get_actions()
                if available:
                    # 若所有按钮 value 为 '放弃'，说明是知晓型阶段（无主动技能）
                    buttons = available[0].get('buttons', [])
                    if all(b.get('value') == '放弃' for b in buttons):
                        ri.select_act_target('放弃')
                        return True
                    # 双刀第一刀（name='mw_knife_first'）
                    if available[0].get('name') == 'mw_knife_first':
                        candidates = [u for u in self.alive_players() if u.nick != user.nick]
                        if candidates:
                            first = random.choice(candidates)
                            rv = ri.select_first_knife_target(f"{first.seat}. {first.nick}")
                            if rv == 'PENDING':
                                # 选第二刀（不同目标）
                                second_candidates = [u for u in self.alive_players()
                                                     if u.nick != user.nick and u.nick != first.nick]
                                if second_candidates:
                                    second = random.choice(second_candidates)
                                    rv2 = ri.select_act_target(f"{second.seat}. {second.nick}")
                                    if rv2 == 'PENDING':
                                        ri.confirm()
                                else:
                                    ri.select_act_target('放弃')
                        else:
                            ri.skip()
                        return True
                    # 普通目标选择
                    candidates = [u for u in self.alive_players() if u.nick != user.nick]
                    if candidates:
                        target = random.choice(candidates)
                        rv = ri.select_act_target(f"{target.seat}. {target.nick}")
                        if rv == 'PENDING':
                            ri.confirm()
                        return True
                ri.skip()
                return True
        self.room.waiting = False
        return True

    def _act_magic_mirror_girl(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                # 指定夜晚强制查验机械狼，其他夜晚随机（排除已查验）
                if self.room.round == self.mmg_checks_mw_night:
                    mw = find_by_role(self.room, Role.MECHANICAL_WOLF)
                    target = mw if mw and mw.nick != user.nick else None
                    if not target:
                        verified = user.skill.get('verified_players', set())
                        candidates = [u for u in self.alive_players()
                                      if u.nick != user.nick and u.nick not in verified]
                        target = random.choice(candidates) if candidates else None
                else:
                    verified = user.skill.get('verified_players', set())
                    candidates = [u for u in self.alive_players()
                                  if u.nick != user.nick and u.nick not in verified]
                    target = random.choice(candidates) if candidates else None

                if target:
                    rv = ri.verify_player(f"{target.seat}. {target.nick}")
                    if rv == 'PENDING':
                        ri.confirm()
                else:
                    ri.skip()
                return True
        self.room.waiting = False
        return True

    # ── Sheriff ───────────────────────────────────────────────────────────

    def _handle_sheriff(self):
        state = self.room.sheriff_state or {}
        phase = state.get('phase')
        if phase is None or phase == 'done':
            return

        if phase == 'signup':
            for nick in self.room._sheriff_signup_pool():
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_voted'):
                    choice = random.choice(['上警', '不上警'])
                    self.room.record_sheriff_choice(user, choice)

        elif phase == 'deferred_withdraw':
            self.room.complete_deferred_withdraw()

        elif phase == 'speech':
            speaker = self.room.current_speaker
            if speaker:
                self.room.advance_sheriff_speech(speaker)

        elif phase == 'await_vote':
            self.room.start_sheriff_vote(pk_mode=False)
            state = self.room.sheriff_state or {}
            candidates = self.room.get_active_sheriff_candidates()
            for nick in state.get('eligible_voters', []):
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

        elif phase == 'vote':
            candidates = self.room.get_active_sheriff_candidates()
            for nick in state.get('eligible_voters', []):
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

        elif phase == 'pk_speech':
            speaker = self.room.current_speaker
            if speaker:
                self.room.advance_sheriff_speech(speaker)

        elif phase in ('await_pk_vote', 'pk_vote'):
            self.room.start_sheriff_vote(pk_mode=True)
            state = self.room.sheriff_state or {}
            candidates = self.room.get_active_sheriff_candidates()
            for nick in state.get('eligible_voters', []):
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

    # ── Day ───────────────────────────────────────────────────────────────

    async def _handle_day(self):
        state = self.room.day_state or {}
        phase = state.get('phase')
        if phase is None or phase == 'done':
            return

        if phase == 'announcement':
            await self.room.publish_night_info()

        elif phase == 'await_sheriff_order':
            s = self.sheriff()
            if s:
                self.room.set_sheriff_order(s, '顺序发言')
            else:
                self.room.day_state['phase'] = 'await_exile_vote'

        elif phase in ('exile_speech', 'exile_pk_speech'):
            self.room.advance_exile_speech()

        elif phase == 'await_exile_vote':
            self.room.start_exile_vote(pk_mode=False)

        elif phase == 'exile_vote':
            candidates = state.get('vote_candidates', [])
            eligible = state.get('eligible_voters', [])
            for nick in eligible:
                user = self.room.players.get(nick)
                if user and not user.skill.get('exile_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_exile_vote(user, target)

        elif phase == 'await_exile_pk_vote':
            self.room.start_exile_vote(pk_mode=True)

        elif phase == 'exile_pk_vote':
            candidates = state.get('pk_candidates', [])
            eligible = state.get('eligible_voters', [])
            for nick in eligible:
                user = self.room.players.get(nick)
                if user and not user.skill.get('exile_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_exile_vote(user, target)

        elif phase == 'last_words':
            current = state.get('current_last_word')
            if current and current in self.room.players:
                user = self.room.players[current]
                ri = user.role_instance
                # Hunter 主动开枪模式（已选 '发动技能'，等待选目标）
                if ri and hasattr(ri, 'in_shoot_mode') and ri.in_shoot_mode():
                    self._hunter_shoot(user, ri)
                # MW 猎人技能开枪模式（private 方法，通过 get_actions 判断）
                elif ri and self._mw_in_shoot_mode(ri):
                    self._hunter_shoot(user, ri)
                elif not user.skill.get('last_words_skill_resolved', False):
                    if (ri and hasattr(ri, 'supports_last_skill')
                            and ri.supports_last_skill()
                            and user.skill.get('can_shoot', True)):
                        self.room.handle_last_word_skill_choice(user, '发动技能')
                    else:
                        self.room.handle_last_word_skill_choice(user, '放弃技能')
                elif not user.skill.get('last_words_done', False):
                    self.room.complete_last_word_speech(user)

        elif phase in ('badge_transfer', 'badge_transfer_done'):
            s = self.sheriff()
            if s:
                self.room.handle_sheriff_badge_action(s, '不传')
            else:
                self.room.day_state['phase'] = 'done'

    def _mw_in_shoot_mode(self, ri) -> bool:
        """通过 get_actions() 返回值判断机械狼是否处于猎人技能开枪模式。"""
        try:
            available = ri.get_actions()
            return bool(available and available[0].get('name') == 'mw_shoot_target')
        except Exception:
            return False

    def _hunter_shoot(self, user, ri):
        """猎人/机械狼（猎人技能）随机射杀一名存活玩家。"""
        other_targets = [u for u in self.alive_players() if u.nick != user.nick]
        target = random.choice(other_targets) if other_targets else None
        if target:
            ri.select_shoot_target(f"{target.seat}. {target.nick}")
            ri.confirm_shoot('confirm')
        else:
            ri.select_shoot_target('cancel_shot')

    async def run(self):
        while not self.room.game_over:
            await asyncio.sleep(0.02)
            if self.room.waiting:
                self._handle_waiting()
                continue
            self._handle_sheriff()
            await self._handle_day()


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_scenario(label: str, forced_mw_learn_role: Role, *,
                       force_wolves_kill_mw_night: int = None,
                       mmg_checks_mw_night: int = 1,
                       seed: int = 42):
    print(f'\n{"="*60}')
    print(f'  {label}')
    print(f'{"="*60}\n')

    for _m in _ASYNC_SLEEP_MODULES:
        _m.async_sleep = _fast_sleep
    _DGF.wait_for_player = _fast_wait_for_player

    random.seed(seed)

    users = make_users(12)
    room = setup_room('preset_mechanical_wolf_mirror', users)

    print(f'房间 {room.id}：{len(room.players)} 名玩家')

    watcher = ScenarioWatcher(
        room, forced_mw_learn_role,
        force_wolves_kill_mw_night=force_wolves_kill_mw_night,
        mmg_checks_mw_night=mmg_checks_mw_night,
    )
    watcher_task = asyncio.create_task(watcher.run())

    print('\n--- 游戏开始 ---')
    await room.start_game()
    cursor = 0
    cursor = print_log(room, cursor)
    print_roles(room)

    elapsed = 0.0
    while not room.game_over and elapsed < 60:
        await asyncio.sleep(0.1)
        elapsed += 0.1
        cursor = print_log(room, cursor)

    watcher_task.cancel()
    cursor = print_log(room, cursor)
    print_roles(room)

    if room.game_over:
        print('=== 游戏结束 ===')
    else:
        print(f'=== 超时停止（{elapsed:.0f}s，stage={room.stage}）===')

    for _m in _ASYNC_SLEEP_MODULES:
        _m.async_sleep = _original_async_sleep
    _DGF.wait_for_player = _original_wait_for_player


async def main():
    await run_scenario(
        '场景A：机械狼第1晚学习通灵师，通灵师第1晚查验机械狼',
        forced_mw_learn_role=Role.MAGIC_MIRROR_GIRL,
        seed=42,
    )

    await run_scenario(
        '场景B：机械狼第1晚学习女巫，通灵师第1晚查验机械狼',
        forced_mw_learn_role=Role.WITCH,
        seed=99,
    )

    await run_scenario(
        '场景C：机械狼第1晚学习守卫（机械盾），通灵师第1晚查验机械狼',
        forced_mw_learn_role=Role.GUARD,
        seed=77,
    )

    await run_scenario(
        '场景D：机械狼第1晚学习猎人，通灵师第1晚查验机械狼，第2晚狼刀机械狼',
        forced_mw_learn_role=Role.HUNTER,
        force_wolves_kill_mw_night=2,
        seed=55,
    )


if __name__ == '__main__':
    asyncio.run(main())
