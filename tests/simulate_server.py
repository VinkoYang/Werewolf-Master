"""
Server-layer simulation – every player action goes through server._dispatch_action,
mirroring the exact path a real button click takes in the browser.

Unlike simulate_12p.py (which calls role methods directly), this script exercises:
  • _compute_actions(user, room)  – button visibility logic
  • _dispatch_action(user, room, data) – button handler dispatch
  • All host buttons: 公布昨夜信息, 警长投票, 放逐投票, 放逐PK投票
  • All player buttons: 上警/不上警, 警长投票, 放逐投票, 发言完毕, 遗言, 警徽移交

The server does NOT need to be running.

Usage (from project root):
    python -m tests.simulate_server --preset preset_standard_12 --auto
    python -m tests.simulate_server --preset preset_dev_3 --auto
"""

import asyncio
import argparse
import random
import sys
import os
from unittest.mock import AsyncMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── Speed patches applied before importing game or server modules ──────────────
import utils as _utils_mod
_original_async_sleep = _utils_mod.async_sleep

async def _fast_sleep(seconds: float):
    await asyncio.sleep(min(seconds, 0.01))

from presets.base import DefaultGameFlow as _DGF
_original_wait_for_player = _DGF.wait_for_player

async def _fast_wait_for_player(self, *, min_duration=None, auto_release=False, silent_timeout=False):
    return await _original_wait_for_player(
        self, min_duration=0, auto_release=auto_release, silent_timeout=silent_timeout
    )

# ── Mock sio.emit so push_state / push_room_state_all become no-ops ───────────
# server.py creates `sio` at import time; patch emit after import.
import socketio as _sio_pkg
_orig_asgi = _sio_pkg.ASGIApp
_sio_pkg.ASGIApp = lambda *a, **kw: None   # avoid ASGI setup error

import server as _server

_sio_pkg.ASGIApp = _orig_asgi
_server.sio.emit = AsyncMock()             # silence all emit calls

# ── Game model imports ────────────────────────────────────────────────────────
from models.system import Global
from models.user import User
from models.room import Room
from models.lobby import resolve_room_config, build_roles_from_config, ROOM_PRESET_CONFIGS
from enums import PlayerStatus, GameStage, Role
from presets.base import WOLF_TEAM_ROLES

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
        room.add_player(user)
    return room


def print_log(room: Room, since: int) -> int:
    for sender, content in room.log[since:]:
        text = content.get('text', '') if isinstance(content, dict) else str(content)
        tag = f'[{sender}]' if sender else '[SYS]'
        if text:
            print(f'{tag} {text}')
    return len(room.log)


def print_roles(room: Room):
    print('\n--- Players ---')
    for user in room.players.values():
        role = user.role.value if user.role else '?'
        status = '' if user.status == PlayerStatus.ALIVE else ' ✝'
        print(f'  {user.seat:2}. {user.nick}: {role}{status}')
    print()


# ── Network-layer watcher ─────────────────────────────────────────────────────

class NetworkWatcher:
    """
    Drives the game by computing available buttons via _compute_actions and
    dispatching them via _dispatch_action – the same path a real button click
    takes from the browser through server.py.
    """

    def __init__(self, room: Room):
        self.room = room
        self._seer_checked: set = set()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def alive_players(self) -> list:
        return [u for u in self.room.players.values() if u.status == PlayerStatus.ALIVE]

    def non_wolves(self) -> list:
        return [u for u in self.alive_players() if u.role not in WOLF_TEAM_ROLES]

    def host(self) -> User:
        return self.room.get_host()

    async def _dispatch(self, user: User, data: dict):
        """Call _dispatch_action directly, skipping the socket layer."""
        await _server._dispatch_action(user, self.room, data)

    def _buttons(self, user: User, action_name: str) -> list:
        """Return non-disabled button values for a named action group."""
        for act in _server._compute_actions(user, self.room):
            if act.get('name') == action_name:
                return [
                    btn['value'] for btn in act.get('buttons', [])
                    if not btn.get('disabled')
                ]
        return []

    def _has_action(self, user: User, action_name: str) -> bool:
        return bool(self._buttons(user, action_name))

    # ── Night phase ───────────────────────────────────────────────────────────

    async def _handle_waiting(self):
        stage = self.room.stage
        if stage == GameStage.WOLF:
            await self._act_wolves()
        elif stage == GameStage.SEER:
            await self._act_seer()
        elif stage == GameStage.WITCH:
            await self._act_witch()
        elif stage == GameStage.GUARD:
            await self._act_guard()
        elif stage == GameStage.HUNTER:
            await self._act_hunter_night()
        else:
            await self._act_generic_night()

    async def _act_wolves(self):
        targets = [f"{u.seat}. {u.nick}" for u in self.non_wolves()]
        for user in list(self.room.players.values()):
            ri = user.role_instance
            if not ri or not ri.should_act():
                continue
            btns = self._buttons(user, 'wolf_team_op')
            if not btns:
                continue
            # Prefer a non-wolf, non-abstain target
            pref = [v for v in btns if v in targets]
            choice = random.choice(pref) if pref else random.choice(btns)
            await self._dispatch(user, {'wolf_team_op': choice})

    async def _act_seer(self):
        for user in list(self.room.players.values()):
            ri = user.role_instance
            if not ri or not ri.should_act():
                continue
            btns = self._buttons(user, 'seer_team_op')
            unchecked = [v for v in btns if v != '放弃' and v not in self._seer_checked
                         and v != f"{user.seat}. {user.nick}"]
            if unchecked:
                target = random.choice(unchecked)
                self._seer_checked.add(target)
                await self._dispatch(user, {'seer_team_op': target})
                # Confirm the selection
                await self._dispatch(user, {'confirm_action': '确认'})
            elif btns:
                await self._dispatch(user, {'seer_team_op': '放弃'})

    async def _act_witch(self):
        for user in list(self.room.players.values()):
            ri = user.role_instance
            if not ri or not ri.should_act():
                continue

            # Heal?
            if self._has_action(user, 'witch_heal_confirm'):
                await self._dispatch(user, {'witch_heal_confirm': 'confirm_heal'})
                return

            # Poison?
            poison_btns = [v for v in self._buttons(user, 'witch_poison_op')
                           if v != 'cancel_poison']
            if poison_btns:
                target = random.choice(poison_btns)
                await self._dispatch(user, {'witch_poison_op': target})
                # Confirm if the confirm button appeared
                if self._has_action(user, 'witch_poison_confirm'):
                    await self._dispatch(user, {'witch_poison_confirm': 'confirm_poison'})
                return

            # Skip
            await self._dispatch(user, {'witch_skip_stage': 'skip_stage'})

    async def _act_guard(self):
        for user in list(self.room.players.values()):
            ri = user.role_instance
            if not ri or not ri.should_act():
                continue
            btns = [v for v in self._buttons(user, 'guard_team_op') if v != '放弃']
            last = user.skill.get('last_protect')
            pref = [v for v in btns if v != f"{self.room.players.get(last, user).seat}. {last}"]
            choice = random.choice(pref) if pref else (random.choice(btns) if btns else '放弃')
            await self._dispatch(user, {'guard_team_op': choice})
            if choice != '放弃':
                await self._dispatch(user, {'confirm_action': '确认'})

    async def _act_hunter_night(self):
        for user in list(self.room.players.values()):
            ri = user.role_instance
            if not ri or not ri.should_act():
                continue
            if self._has_action(user, 'hunter_confirm'):
                await self._dispatch(user, {'hunter_confirm': '确认'})

    async def _act_generic_night(self):
        """Fallback for special roles (梦魇, 狼美人, 半血, etc.)."""
        for user in list(self.room.players.values()):
            ri = user.role_instance
            if not ri or not ri.should_act():
                continue
            # Try every action group and pick a random non-disabled button
            for act in _server._compute_actions(user, self.room):
                btns = [btn['value'] for btn in act.get('buttons', []) if not btn.get('disabled')]
                if btns:
                    await self._dispatch(user, {act['name']: random.choice(btns)})
                    break
            else:
                user.skip(reason='timeout')

    # ── Sheriff phase ─────────────────────────────────────────────────────────

    async def _handle_sheriff(self):
        state = self.room.sheriff_state or {}
        phase = state.get('phase')
        if not phase or phase == 'done':
            return

        if phase == 'signup':
            for user in list(self.room.players.values()):
                if user.skill.get('sheriff_voted'):
                    continue
                if self._has_action(user, 'sheriff_vote'):
                    choice = '上警' if user.role == Role.SEER else random.choice(['上警', '不上警'])
                    await self._dispatch(user, {'sheriff_vote': choice})

        elif phase == 'speech':
            speaker = self.room.current_speaker
            if speaker:
                user = self.room.players.get(speaker)
                if user and self._has_action(user, 'speech_done'):
                    await self._dispatch(user, {'speech_done': '发言完毕'})

        elif phase == 'await_vote':
            host = self.host()
            if host and self._has_action(host, 'sheriff_host_action'):
                await self._dispatch(host, {'sheriff_host_action': '警长投票'})
                # Cast votes immediately (timer fires before next loop tick)
                await self._cast_sheriff_votes()

        elif phase == 'vote':
            await self._cast_sheriff_votes()

        elif phase == 'pk_speech':
            speaker = self.room.current_speaker
            if speaker:
                user = self.room.players.get(speaker)
                if user and self._has_action(user, 'speech_done'):
                    await self._dispatch(user, {'speech_done': '发言完毕'})

        elif phase == 'await_pk_vote':
            host = self.host()
            if host and self._has_action(host, 'sheriff_host_action'):
                await self._dispatch(host, {'sheriff_host_action': '警长PK投票'})
                await self._cast_sheriff_votes()

        elif phase == 'pk_vote':
            await self._cast_sheriff_votes()

    async def _cast_sheriff_votes(self):
        state = self.room.sheriff_state or {}
        candidates = self.room.get_active_sheriff_candidates()
        for nick in state.get('eligible_voters', []):
            user = self.room.players.get(nick)
            if not user or user.skill.get('sheriff_has_balloted'):
                continue
            btns = self._buttons(user, 'sheriff_ballot')
            valid = [v for v in btns if v != '弃票']
            if not valid and candidates:
                valid = candidates  # fall back to nick list (dispatch strips label)
            target = random.choice(valid) if valid else '弃票'
            await self._dispatch(user, {'sheriff_ballot': target})

    # ── Day phase ─────────────────────────────────────────────────────────────

    async def _handle_day(self):
        state = self.room.day_state or {}
        phase = state.get('phase')
        if not phase or phase == 'done':
            return

        host = self.host()

        if phase == 'announcement':
            if host and self._has_action(host, 'day_host_action'):
                await self._dispatch(host, {'day_host_action': '公布昨夜信息'})

        elif phase == 'await_sheriff_order':
            sheriff_nick = self.room.skill.get('sheriff_captain')
            if sheriff_nick:
                user = self.room.players.get(sheriff_nick)
                if user and self._has_action(user, 'sheriff_set_order'):
                    await self._dispatch(user, {'sheriff_set_order': '顺序发言'})
            else:
                self.room.day_state['phase'] = 'await_exile_vote'

        elif phase in ('exile_speech', 'exile_pk_speech'):
            speaker = getattr(self.room, 'current_speaker', None)
            if speaker:
                user = self.room.players.get(speaker)
                if user and self._has_action(user, 'speech_done'):
                    await self._dispatch(user, {'speech_done': '发言完毕'})

        elif phase == 'await_exile_vote':
            if host and self._has_action(host, 'day_host_action'):
                await self._dispatch(host, {'day_host_action': '放逐投票'})
                await self._cast_exile_votes(pk=False)

        elif phase == 'exile_vote':
            await self._cast_exile_votes(pk=False)

        elif phase == 'await_exile_pk_vote':
            if host and self._has_action(host, 'day_host_action'):
                await self._dispatch(host, {'day_host_action': '放逐PK投票'})
                await self._cast_exile_votes(pk=True)

        elif phase == 'exile_pk_vote':
            await self._cast_exile_votes(pk=True)

        elif phase == 'last_words':
            await self._handle_last_words()

        elif phase in ('badge_transfer', 'badge_transfer_done'):
            await self._handle_badge_transfer()

    async def _cast_exile_votes(self, pk: bool):
        state = self.room.day_state or {}
        key = 'pk_candidates' if pk else 'vote_candidates'
        candidates = state.get(key, [])
        for nick in state.get('eligible_voters', []):
            user = self.room.players.get(nick)
            if not user or user.skill.get('exile_has_balloted'):
                continue
            btns = self._buttons(user, 'exile_vote')
            valid = [v for v in btns if v != '弃票']
            if not valid and candidates:
                # Build label format from nick list as fallback
                valid = []
                for cn in candidates:
                    p = self.room.players.get(cn)
                    if p:
                        valid.append(f"{p.seat}. {cn}")
            target = random.choice(valid) if valid else '弃票'
            await self._dispatch(user, {'exile_vote': target})

    async def _handle_last_words(self):
        state = self.room.day_state or {}
        current = state.get('current_last_word')
        if not current or current not in self.room.players:
            return
        user = self.room.players[current]
        ri = user.role_instance

        # Hunter/wolf_king in active shoot mode
        if ri and hasattr(ri, 'in_shoot_mode') and ri.in_shoot_mode():
            await self._hunter_shoot(user)
            return

        # Skill choice (发动/放弃)
        if self._has_action(user, 'last_word_skill'):
            can_trigger = (ri and hasattr(ri, 'supports_last_skill') and
                           ri.supports_last_skill() and user.skill.get('can_shoot', True))
            choice = '发动技能' if can_trigger else '放弃'
            btns = self._buttons(user, 'last_word_skill')
            if choice not in btns:
                choice = btns[0] if btns else '放弃'
            await self._dispatch(user, {'last_word_skill': choice})
            return

        # Hunter/wolf_king picked 发动技能 → now in shoot mode
        if ri and hasattr(ri, 'in_shoot_mode') and ri.in_shoot_mode():
            await self._hunter_shoot(user)
            return

        # Finish last words speech
        if self._has_action(user, 'last_word_done'):
            await self._dispatch(user, {'last_word_done': '遗言结束'})

    async def _hunter_shoot(self, user: User):
        """Hunter or wolf_king picks a random alive player to shoot."""
        btns = self._buttons(user, 'hunter_shoot_target')
        valid = [v for v in btns if v != 'cancel_shot']
        if valid:
            target = random.choice(valid)
            await self._dispatch(user, {'hunter_shoot_target': target})
            if self._has_action(user, 'hunter_shoot_confirm'):
                await self._dispatch(user, {'hunter_shoot_confirm': 'confirm'})
        else:
            await self._dispatch(user, {'hunter_shoot_target': 'cancel_shot'})

    async def _handle_badge_transfer(self):
        sheriff_nick = self.room.skill.get('sheriff_captain')
        if not sheriff_nick:
            self.room.day_state['phase'] = 'done'
            return
        user = self.room.players.get(sheriff_nick)
        if not user:
            return
        btns = self._buttons(user, 'sheriff_badge_action')
        if btns:
            await self._dispatch(user, {'sheriff_badge_action': random.choice(btns)})

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        while not self.room.game_over:
            await asyncio.sleep(0.02)

            if self.room.waiting:
                await self._handle_waiting()
                continue

            await self._handle_sheriff()
            await self._handle_day()


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(preset: str, num_players: int, auto: bool):
    print(f'\n=== Server-layer simulation: {preset}, {num_players} players ===\n')

    config = resolve_room_config(preset)
    if config is None:
        print(f'ERROR: Unknown preset {preset!r}')
        return

    needed = len(build_roles_from_config(config))
    if num_players < needed:
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
        watcher = NetworkWatcher(room)
        watcher_task = asyncio.create_task(watcher.run())

    print('\n--- Starting game ---')
    await room.start_game()
    cursor = print_log(room, cursor)
    print_roles(room)

    if not auto:
        print('Rerun with --auto to drive the game end-to-end.')
        return

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
        description='Simulate via server._dispatch_action (tests button dispatch path)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--preset', default='preset_standard_12')
    parser.add_argument('--players', type=int, default=12)
    parser.add_argument('--auto', action='store_true')
    args = parser.parse_args()
    asyncio.run(run(args.preset, args.players, args.auto))


if __name__ == '__main__':
    main()
