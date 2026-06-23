"""Tests for the per-user countdown task in server.py."""
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from models.room import Room
from models.user import User
from models.system import Global
from enums import GameStage


def _make_room_and_users():
    u1 = User.alloc('Alice', 's1', 't1')
    u2 = User.alloc('Bob',   's2', 't2')
    u3 = User.alloc('Carol', 's3', 't3')
    config = {
        'wolf_num': 1, 'citizen_num': 1, 'god_wolf': [], 'god_citizen': ['预言家'],
        'witch_rule': '仅第一夜可自救', 'guard_rule': '同时被守被救时，对象死亡',
        'sheriff_bomb_rule': '双爆吞警徽',
    }
    room = Room.alloc(config)
    for u in (u1, u2, u3):
        room.add_player(u)
    return room, u1, u2, u3


def _cleanup(*users):
    for u in users:
        Global.users.pop(u.nick, None)


def test_countdown_emits_tick_then_clear():
    """_countdown sends countdown_tick each second and countdown_clear at the end."""
    emitted = []

    async def fake_emit(event, data=None, to=None):
        emitted.append((event, data))

    async def run():
        from server import _countdown
        room, u1, u2, u3 = _make_room_and_users()
        u1.sid = 'sid1'
        u1.skill['countdown_skip_timeout'] = False  # allow timeout to fire

        with patch('server.sio') as mock_sio:
            mock_sio.emit = fake_emit
            # Run a 3-second countdown
            await _countdown(u1, seconds=3)

        _cleanup(u1, u2, u3)

    asyncio.run(run())

    tick_events = [d['seconds'] for e, d in emitted if e == 'countdown_tick']
    clear_events = [e for e, d in emitted if e == 'countdown_clear']

    assert tick_events == [3, 2, 1], f"Unexpected ticks: {tick_events}"
    assert len(clear_events) == 1, "Expected exactly one countdown_clear"


def test_countdown_respects_skip_flag():
    """Setting countdown_skip_timeout before countdown ends prevents timeout logic."""
    timeout_fired = []

    async def run():
        from server import _countdown
        room, u1, u2, u3 = _make_room_and_users()
        u1.sid = 'sid1'

        async def fake_emit(event, data=None, to=None):
            # After first tick, set skip flag
            if event == 'countdown_tick' and data.get('seconds') == 2:
                u1.skill['countdown_skip_timeout'] = True

        with patch('server.sio') as mock_sio:
            mock_sio.emit = fake_emit
            await _countdown(u1, seconds=3)

        timeout_fired.append(u1.skill.get('countdown_skip_timeout', False))
        _cleanup(u1, u2, u3)

    asyncio.run(run())
    # Skip flag should have been consumed (popped) inside _countdown
    assert timeout_fired[0] is False
