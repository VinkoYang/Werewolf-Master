"""
12-player simulation – direct injection, no network needed.

The server does NOT need to be running.

Usage (run from project root):
    python -m tests.simulate_12p                              # basic 12p, shows roles then stops
    python -m tests.simulate_12p --preset preset_dev_3       # 3-player quick look
    python -m tests.simulate_12p --preset preset_dev_3 --auto    # auto-play to GAME OVER
    python -m tests.simulate_12p --preset preset_standard_12 --auto

Available presets:
    preset_dev_3  preset_dev_6  preset_dev_7
    preset_standard_12  preset_half_blood_mix  preset_white_wolf_guard
    preset_wolf_king_guard  preset_wolf_king_dreamer
    preset_nine_tailed_fox  preset_nightmare  preset_wolf_beauty
"""

import asyncio
import argparse
import random
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── Patches applied before importing game modules ────────────────────────────
import utils as _utils_mod
_original_async_sleep = _utils_mod.async_sleep

async def _fast_sleep(seconds: float):
    await asyncio.sleep(min(seconds, 0.01))

# ── Game model imports ────────────────────────────────────────────────────────
from presets.base import DefaultGameFlow as _DGF
_original_wait_for_player = _DGF.wait_for_player

async def _fast_wait_for_player(self, *, min_duration=None, auto_release=False, silent_timeout=False):
    """In test mode, skip the min-duration guard so actions resolve instantly."""
    return await _original_wait_for_player(
        self, min_duration=0, auto_release=auto_release, silent_timeout=silent_timeout
    )

from models.system import Global
from models.user import User
from models.room import Room
from models.lobby import resolve_room_config, build_roles_from_config, ROOM_PRESET_CONFIGS
from enums import PlayerStatus, GameStage, Role
from presets.base import WOLF_TEAM_ROLES, WOLF_CAMP_ROLES

# Collect all modules that bind async_sleep locally (via `from utils import async_sleep`).
# Patching _utils_mod.async_sleep alone doesn't reach these because each module already
# holds its own reference to the original function in its globals.
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


# ── Setup helpers ─────────────────────────────────────────────────────────────

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
    if config is None:
        raise ValueError(
            f'Unknown preset: {preset!r}\n'
            f'Available: {list(ROOM_PRESET_CONFIGS.keys())}'
        )
    needed = len(build_roles_from_config(config))
    if len(users) < needed:
        raise ValueError(f'Preset {preset!r} needs {needed} players, got {len(users)}')
    room = Room.alloc(config)
    for user in users[:needed]:
        room.add_player(user)   # auto-assigns next free seat
    return room


def print_log(room: Room, since: int, prefix: str = '') -> int:
    for sender, content in room.log[since:]:
        text = content.get('text', '') if isinstance(content, dict) else str(content)
        tag = f'[{sender}]' if sender else '[SYS]'
        if text:
            print(f'{prefix}{tag} {text}')
    return len(room.log)


def print_roles(room: Room):
    print('\n--- Players ---')
    for user in room.players.values():
        role = user.role.value if user.role else '?'
        status = '' if user.status == PlayerStatus.ALIVE else f' ✝'
        print(f'  {user.seat:2}. {user.nick}: {role}{status}')
    print()


# ── Auto-play watcher ─────────────────────────────────────────────────────────

class AutoWatcher:
    """
    Drives all game phases with realistic bot behaviour:

    Night roles
    -----------
    Wolves    – each wolf independently picks a random non-wolf target every night.
    Seer      – checks a random unchecked player each night; always runs for sheriff.
    Witch     – saves the wolf-killed player on the first night she has heal;
                poisons a random surviving player (excluding self) when she has poison.
    Guard     – protects a random living player, never two nights running.
    Hunter    – confirms gun status at night; shoots a random surviving player when exiled.

    Day phases
    ----------
    Sheriff   – each player randomly decides to run or not (Seer always runs);
                eligible voters each independently pick a random candidate.
    Speeches  – advance every speaker instantly.
    Exile vote– each voter independently picks a random candidate (inc. PK rounds).
    Last words– passive skill resolved; hunter shoots a random wolf (or any
                alive player) before giving last words.
    Badge     – sheriff keeps badge (不传), or forced done when no sheriff.
    """

    def __init__(self, room: Room):
        self.room = room
        self._seer_checked: set = set()   # nicks the seer has already checked

    def alive_players(self) -> list:
        return [u for u in self.room.players.values() if u.status == PlayerStatus.ALIVE]

    def acting_players(self) -> list:
        """Players who can still act: alive or in near-death (pending kill) state."""
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

    # ── Night / waiting ───────────────────────────────────────────────────────

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
        # Fallback for special roles (梦魇, 半血, 狼美人, 狼王, etc.)
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                user.skip(reason='timeout')
                return True
        self.room.waiting = False
        return True

    def _act_wolves(self):
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
                    label = f"{target.seat}. {target.nick}"
                    rv = ri.identify_player(label)
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
                        label = f"{target.seat}. {target.nick}"
                        rv = ri.select_poison_target(label)
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
                    label = f"{target.seat}. {target.nick}"
                    rv = ri.protect_player(label)
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
                candidates = [u for u in self.alive_players() if u.nick != user.nick]
                if candidates:
                    target = random.choice(candidates)
                    label = f"{target.seat}. {target.nick}"
                    rv = ri.select_learn_target(label)
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
                    # If all buttons are '放弃', this is an acknowledge-only phase (no active skill)
                    buttons = available[0].get('buttons', [])
                    if all(b.get('value') == '放弃' for b in buttons):
                        ri.select_act_target('放弃')
                        return True
                    candidates = [u for u in self.alive_players() if u.nick != user.nick]
                    if candidates:
                        target = random.choice(candidates)
                        label = f"{target.seat}. {target.nick}"
                        rv = ri.select_act_target(label)
                        if rv == 'PENDING':
                            ri.confirm()
                        return True
                # No active skill this phase
                ri.skip()
                return True
        self.room.waiting = False
        return True

    def _act_magic_mirror_girl(self):
        for user in self.acting_players():
            ri = user.role_instance
            if ri and ri.should_act():
                verified = user.skill.get('verified_players', set())
                candidates = [u for u in self.alive_players()
                              if u.nick != user.nick and u.nick not in verified]
                if candidates:
                    target = random.choice(candidates)
                    label = f"{target.seat}. {target.nick}"
                    rv = ri.verify_player(label)
                    if rv == 'PENDING':
                        ri.confirm()
                else:
                    ri.skip()
                return True
        self.room.waiting = False
        return True

    # ── Sheriff phase ─────────────────────────────────────────────────────────

    def _handle_sheriff(self):
        state = self.room.sheriff_state or {}
        phase = state.get('phase')
        if phase is None or phase == 'done':
            return

        if phase == 'signup':
            # Seer always runs; everyone else decides randomly.
            for nick in self.room._sheriff_signup_pool():
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_voted'):
                    if user.role == Role.SEER:
                        choice = '上警'
                    else:
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
            # Cast votes immediately — the fast-sleep timer fires before the next loop tick.
            state = self.room.sheriff_state or {}
            candidates = self.room.get_active_sheriff_candidates()
            for nick in state.get('eligible_voters', []):
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

        elif phase == 'vote':
            candidates = self.room.get_active_sheriff_candidates()
            eligible = state.get('eligible_voters', [])
            for nick in eligible:
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

        elif phase == 'pk_speech':
            speaker = self.room.current_speaker
            if speaker:
                self.room.advance_sheriff_speech(speaker)

        elif phase == 'await_pk_vote':
            self.room.start_sheriff_vote(pk_mode=True)
            # Cast votes immediately — the fast-sleep timer fires before the next loop tick.
            state = self.room.sheriff_state or {}
            candidates = self.room.get_active_sheriff_candidates()
            for nick in state.get('eligible_voters', []):
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

        elif phase == 'pk_vote':
            candidates = self.room.get_active_sheriff_candidates()
            eligible = state.get('eligible_voters', [])
            for nick in eligible:
                user = self.room.players.get(nick)
                if user and not user.skill.get('sheriff_has_balloted'):
                    target = random.choice(candidates) if candidates else '弃票'
                    self.room.record_sheriff_ballot(user, target)

    # ── Day phase ─────────────────────────────────────────────────────────────

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
                # Hunter in active shoot mode (chose '发动技能' on previous tick)
                if ri and hasattr(ri, 'in_shoot_mode') and ri.in_shoot_mode():
                    self._hunter_shoot(user, ri)
                elif not user.skill.get('last_words_skill_resolved', False):
                    # Hunter can shoot — let them; everyone else gives up skill
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

    def _hunter_shoot(self, user, ri):
        """Hunter picks a random surviving player (excluding self) to shoot."""
        other_targets = [u for u in self.alive_players() if u.nick != user.nick]
        target = random.choice(other_targets) if other_targets else None
        if target:
            label = f"{target.seat}. {target.nick}"
            ri.select_shoot_target(label)
            ri.confirm_shoot('confirm')
        else:
            ri.select_shoot_target('cancel_shot')

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        while not self.room.game_over:
            await asyncio.sleep(0.02)

            if self.room.waiting:
                self._handle_waiting()
                continue

            self._handle_sheriff()
            await self._handle_day()


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(preset: str, num_players: int, auto: bool):
    print(f'\n=== Werewolf simulation: {preset}, {num_players} players ===\n')

    config = resolve_room_config(preset)
    if config is None:
        print(f'ERROR: Unknown preset {preset!r}')
        print('Available:', ', '.join(ROOM_PRESET_CONFIGS.keys()))
        return

    needed = len(build_roles_from_config(config))
    if num_players < needed:
        print(f'Preset needs {needed} players – adjusting from {num_players}.')
        num_players = needed

    if auto:
        for _m in _ASYNC_SLEEP_MODULES:
            _m.async_sleep = _fast_sleep
        _DGF.wait_for_player = _fast_wait_for_player

    users = make_users(num_players)
    room = setup_room(preset, users)
    print(f'Room {room.id}: {len(room.players)} players / {len(room.roles)} roles.')

    cursor = 0

    watcher_task = None
    if auto:
        watcher = AutoWatcher(room)
        watcher_task = asyncio.create_task(watcher.run())

    print('\n--- Starting game ---')
    await room.start_game()
    cursor = print_log(room, cursor)
    print_roles(room)

    if not auto:
        print('Rerun with --auto to drive the game end-to-end.')
        return

    # Wait up to 60 seconds for GAME OVER
    elapsed = 0.0
    while not room.game_over and elapsed < 60:
        await asyncio.sleep(0.1)
        elapsed += 0.1
        cursor = print_log(room, cursor)

    if watcher_task:
        watcher_task.cancel()
    cursor = print_log(room, cursor)
    print_roles(room)

    if room.game_over:
        print('=== GAME OVER ===')
    else:
        print(f'=== Stopped after {elapsed:.0f}s (stage={room.stage}, '
              f'sheriff={room.sheriff_state.get("phase") if room.sheriff_state else "-"}, '
              f'day={room.day_state.get("phase") if room.day_state else "-"}) ===')

    for _m in _ASYNC_SLEEP_MODULES:
        _m.async_sleep = _original_async_sleep
    _DGF.wait_for_player = _original_wait_for_player


def main():
    parser = argparse.ArgumentParser(
        description='Simulate a Werewolf game (no server needed)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--preset', default='preset_standard_12')
    parser.add_argument('--players', type=int, default=12)
    parser.add_argument('--auto', action='store_true',
                        help='Auto-play all actions to GAME OVER')
    args = parser.parse_args()
    asyncio.run(run(args.preset, args.players, args.auto))


if __name__ == '__main__':
    main()
