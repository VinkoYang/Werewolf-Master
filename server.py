# server.py – FastAPI + python-socketio entry point (replaces main.py)
import asyncio
import os
import re
import secrets
import sys
import json
from copy import copy
from typing import Optional, Tuple
from logging import getLogger, basicConfig

import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from enums import WitchRule, GuardRule, SheriffBombRule, Role, GameStage, PlayerStatus
from models.room import Room
from models.user import User
from models.system import Global
from models.lobby import (
    ROOM_CREATION_SECTIONS, GAME_RESOURCE_LINKS, GUIDE_LINKS,
    DEV_LINKS, FEEDBACK_LINK, build_room_info_lines, resolve_room_config,
    build_roles_from_config, PRESET_CUSTOM,
)
from utils import get_interface_ip
from presets.game_config_presets import DEFAULT_ROOM_RULES
from stub import actions as _stub_actions

basicConfig(stream=sys.stdout,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = getLogger('Wolf')
logger.setLevel('DEBUG')

# ── Socket.IO + FastAPI setup ────────────────────────────────────────────────

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*', ping_timeout=60, ping_interval=25)
_fastapi = FastAPI()
app = socketio.ASGIApp(sio, _fastapi)

_fastapi.mount('/static', StaticFiles(directory='static'), name='static')

@_fastapi.get('/')
async def index():
    return FileResponse('static/index.html')


# ── Token → User mapping for reconnection ────────────────────────────────────

_token_to_nick: dict[str, str] = {}   # token → nick
_sid_to_nick: dict[str, str] = {}     # sid  → nick


def _user_by_sid(sid: str) -> Optional[User]:
    nick = _sid_to_nick.get(sid)
    return Global.users.get(nick) if nick else None


# ── Helpers: room config ─────────────────────────────────────────────────────

def _room_config_text(room: Room) -> str:
    """Human-readable one-line summary of a room's role and rule configuration."""
    from collections import Counter
    counter = Counter(room.roles)
    if not counter:
        return '暂未配置角色'
    parts = []
    for role, count in sorted(counter.items(), key=lambda item: item[0].value):
        parts.append(f"{role.value}×{count}" if count > 1 else role.value)
    total = len(room.roles)
    rules = (f"女巫：{room.witch_rule.value}｜"
             f"守卫：{room.guard_rule.value}｜"
             f"自曝：{room.sheriff_bomb_rule.value}")
    if Role.MECHANICAL_WOLF in room.roles:
        mw_opts = []
        if getattr(room, 'mw_shield_blocks_hunter', False):
            mw_opts.append('机械盾抵挡猎枪')
        if getattr(room, 'mw_double_knife_breaks_shield', False):
            mw_opts.append('双刀破盾')
        if mw_opts:
            rules += '｜' + '｜'.join(mw_opts)
    return f"共 {total} 人 | {'、'.join(parts)} | {rules}"


def _room_config_dict(room: Room) -> dict:
    """Extract current room config as a dict matching the custom-form format."""
    GOD_WOLF = {Role.WOLF_KING, Role.WHITE_WOLF_KING, Role.NIGHTMARE, Role.WOLF_BEAUTY, Role.MECHANICAL_WOLF}
    GOD_CITIZEN = {Role.SEER, Role.WITCH, Role.GUARD, Role.HUNTER, Role.DREAMER,
                   Role.IDIOT, Role.HALF_BLOOD, Role.NINE_TAILED_FOX, Role.MAGIC_MIRROR_GIRL}
    from collections import Counter
    counter = Counter(room.roles)
    return {
        'wolf_num': counter.get(Role.WOLF, 0),
        'citizen_num': counter.get(Role.CITIZEN, 0),
        'god_wolf': [r.value for r in room.roles if r in GOD_WOLF],
        'god_citizen': [r.value for r in room.roles if r in GOD_CITIZEN],
        'witch_rule': room.witch_rule.value,
        'guard_rule': room.guard_rule.value,
        'sheriff_bomb_rule': room.sheriff_bomb_rule.value,
        'mw_shield_blocks_hunter': getattr(room, 'mw_shield_blocks_hunter', False),
        'mw_double_knife_breaks_shield': getattr(room, 'mw_double_knife_breaks_shield', False),
    }


async def _room_push_loop(room: Room):
    """Push state to all players whenever the game advances (stage change or new log entries)."""
    last_log_len = 0
    last_stage = None
    try:
        while not room.game_over:
            await asyncio.sleep(0.5)
            new_len = len(room.log)
            new_stage = room.stage
            if new_len != last_log_len or new_stage != last_stage:
                last_log_len = new_len
                last_stage = new_stage
                if room.players:
                    await push_room_state_all(room)
    except Exception:
        logger.exception('_room_push_loop 出现异常')
    finally:
        if room.players:
            await push_room_state_all(room)


async def _run_start_game(room: Room):
    """Fire-and-forget wrapper: run start_game then push state to all players."""
    await room.start_game()
    asyncio.create_task(_room_push_loop(room))
    await push_room_state_all(room)


# ── Helpers: state push ───────────────────────────────────────────────────────

async def _reset_room_for_new_game(room: Room):
    """Reset room to pre-game state, removing players who chose not to join."""
    if room.logic_thread and not room.logic_thread.done():
        room.logic_thread.cancel()
    room.logic_thread = None

    quitters = [u for u in list(room.players.values())
                if u.skill.get('play_again_response') == '不参加']

    for user in list(room.players.values()):
        _cancel_countdown(user, suppress_timeout=True)

    room.started = False
    room.game_over = False
    room.round = 0
    room.stage = None
    room.waiting = False
    room.log = []
    room.skill = {}
    room.death_pending = []
    room.current_speaker = None
    room.sheriff_speakers = None
    room.sheriff_speaker_index = 0
    room.sheriff_state = {}
    room.day_state = {}
    room.sheriff_badge_destroyed = False
    room.roles_pool = copy(room.roles)

    for user in list(room.players.values()):
        user.role = None
        user.role_instance = None
        user.status = None
        user.skill = {}
        user.message_cursor = 0

    for user in quitters:
        room.players.pop(user.nick, None)
        user.room = None
        user.seat = None
        await push_state(user)

    if not room.players:
        from models.system import Global as _G
        _G.remove_room(room.id)
        return

    room._mark_seat_state_dirty()
    room.broadcast_msg('=' * 22)
    room.broadcast_msg(f'新局准备就绪！共 {len(room.players)} 名玩家，房主是 {room.get_host().nick}')
    room.broadcast_msg(f'房间配置：{_room_config_text(room)}')
    for user in room.players.values():
        user.message_cursor = 0

    await push_room_state_all(room)


def _compute_actions(user: User, room: Optional[Room]) -> list:
    """Mirror of main.py's per-loop action building, returning stub dicts."""
    ops = []
    if not room:
        return ops

    # ── Play again (after game over) ─────────────────────────────────────────
    if room.game_over:
        if user is room.get_host() and not room.skill.get('play_again_pending'):
            ops += [_stub_actions(name='play_again', buttons=['再来一局'], help_text='点击发起新一局')]
        if room.skill.get('play_again_pending') and not user.skill.get('play_again_response'):
            ops += [_stub_actions(name='play_again_response',
                       buttons=[{'label': '加入新局', 'value': '加入', 'color': 'success'},
                                 {'label': '不参加',   'value': '不参加', 'color': 'secondary'}],
                       help_text='是否参加新一局？')]
        return ops

    sheriff_state = getattr(room, 'sheriff_state', {}) or {}
    day_state = getattr(room, 'day_state', {}) or {}

    # ── Host operations ──────────────────────────────────────────────────────
    if user is room.get_host():
        if not room.started:
            ops += [_stub_actions(name='host_op', buttons=['开始游戏', '房间配置'], help_text='你是房主')]

        if room.stage in (GameStage.SHERIFF, GameStage.SPEECH):
            phase = sheriff_state.get('phase')
            if phase == 'await_vote':
                ops += [_stub_actions(name='sheriff_host_action', buttons=['警长投票'], help_text='发起警长投票')]
            elif phase == 'await_pk_vote':
                ops += [_stub_actions(name='sheriff_host_action', buttons=['警长PK投票'], help_text='发起警长PK投票')]

        if day_state.get('phase') == 'announcement':
            ops += [_stub_actions(name='day_host_action', buttons=['公布昨夜信息'], help_text='公布昨夜死亡情况')]
        elif day_state.get('phase') == 'await_exile_vote':
            ops += [_stub_actions(name='day_host_action', buttons=['放逐投票'], help_text='发起放逐投票')]
        elif day_state.get('phase') == 'await_exile_pk_vote':
            ops += [_stub_actions(name='day_host_action', buttons=['放逐PK投票'], help_text='发起放逐PK投票')]

    # ── Player / role operations ──────────────────────────────────────────────
    if room.started and user.role_instance:
        ops += user.role_instance.get_actions()

        # Sheriff signup
        can_join = (room.can_participate_in_sheriff(user.nick)
                    if hasattr(room, 'can_participate_in_sheriff')
                    else user.status == PlayerStatus.ALIVE)

        if room.stage in (GameStage.SHERIFF, GameStage.SPEECH) and can_join:
            phase = sheriff_state.get('phase')
            if phase == 'signup' and not user.skill.get('sheriff_voted'):
                ops += [_stub_actions(name='sheriff_vote', buttons=['上警', '不上警'],
                           help_text='请选择是否上警（10秒内未选则视为不上警）')]

            active_cands = (room.get_active_sheriff_candidates()
                            if hasattr(room, 'get_active_sheriff_candidates') else [])
            if (phase in ('speech', 'await_vote', 'pk_speech', 'await_pk_vote') and
                    user.nick in active_cands and
                    not user.skill.get('sheriff_withdrawn')):
                ops += [_stub_actions(name='sheriff_withdraw', buttons=['退水'], help_text='退水后将退出竞选')]

            if (phase in ('vote', 'pk_vote') and
                    user.nick in sheriff_state.get('eligible_voters', []) and
                    not user.skill.get('sheriff_has_balloted')):
                btns = []
                for nick in active_cands:
                    p = room.players.get(nick)
                    seat = p.seat if p and p.seat is not None else '?'
                    btns.append({'label': f"{seat}. {nick}", 'value': f"{seat}. {nick}"})
                btns.append({'label': '弃票', 'value': '弃票', 'color': 'secondary'})
                ops += [_stub_actions(name='sheriff_ballot', buttons=btns, help_text='请选择支持的警长候选人')]

        # Last words
        if room.stage == GameStage.LAST_WORDS and day_state.get('current_last_word') == user.nick:
            supports = bool(user.role_instance and
                            hasattr(user.role_instance, 'supports_last_skill') and
                            user.role_instance.supports_last_skill())
            if not user.skill.get('last_words_skill_resolved') and not user.skill.get('pending_last_skill'):
                btns = ['放弃']
                if supports:
                    can_trigger = True
                    if user.role in (Role.HUNTER, Role.WOLF_KING) and not user.skill.get('can_shoot', True):
                        can_trigger = False
                    if can_trigger:
                        btns = ['发动技能', '放弃']
                    else:
                        btns = [{'label': '发动技能（不可用）', 'value': 'disabled_last_skill',
                                 'disabled': True, 'color': 'secondary'}, '放弃']
                ops += [_stub_actions(name='last_word_skill', buttons=btns, help_text='发表遗言前是否发动技能？（10秒）')]
            elif day_state.get('last_words_allow_speech', True) and not user.skill.get('last_words_done'):
                ops += [_stub_actions(name='last_word_done', buttons=['遗言结束'], help_text='发表完遗言后点击')]

        # Sheriff order
        if (day_state.get('phase') == 'await_sheriff_order' and
                room.skill.get('sheriff_captain') == user.nick and
                user.status == PlayerStatus.ALIVE):
            ops += [_stub_actions(name='sheriff_set_order', buttons=['顺序发言', '逆序发言'],
                       help_text='请选择今日发言顺序')]

        # Idiot badge
        if user.skill.get('idiot_badge_transfer_required'):
            candidates = [u for u in room.list_alive_players() if u.nick != user.nick]
            btns = [{'label': f'交给{p.seat}号{p.nick}', 'value': p.nick} for p in candidates]
            if not btns:
                btns.append({'label': '无人可交出，空缺', 'value': 'forfeit', 'color': 'warning'})
            ops += [_stub_actions(name='idiot_badge_transfer', buttons=btns, help_text='白痴必须立即移交警徽')]

        # Badge transfer
        if (room.stage == GameStage.BADGE_TRANSFER and
                room.skill.get('sheriff_captain') == user.nick and
                not user.skill.get('badge_action_taken')):
            alive = [u for u in room.list_alive_players() if u.nick != user.nick]
            btns = [{'label': f'交给{p.seat}号{p.nick}', 'value': f'transfer:{p.nick}'} for p in alive]
            btns.append({'label': '撕毁警徽', 'value': 'destroy', 'color': 'danger'})
            ops += [_stub_actions(name='sheriff_badge_action', buttons=btns, help_text='请选择移交对象或撕毁警徽（10秒）')]

        # Wolf self-bomb
        if room.can_wolf_self_bomb(user):
            ops += [_stub_actions(name='wolf_self_bomb',
                       buttons=[{'label': '自曝', 'value': 'boom', 'color': 'danger'}],
                       help_text='立即结束当前阶段并出局')]

        # Speech done
        if (hasattr(room, 'current_speaker') and
                room.stage in (GameStage.SPEECH, GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH) and
                user.nick == room.current_speaker):
            ops += [_stub_actions(name='speech_done', buttons=['发言完毕'], help_text='点击结束发言')]

        # Exile vote
        if room.stage in (GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE):
            if user.skill.get('exile_vote_pending'):
                btns = []
                for nick in day_state.get('vote_candidates', []):
                    p = room.players.get(nick)
                    seat = p.seat if p and p.seat is not None else '?'
                    btns.append({'label': f"{seat}. {nick}", 'value': f"{seat}. {nick}"})
                btns.append({'label': '弃票', 'value': '弃票', 'color': 'secondary'})
                ops += [_stub_actions(name='exile_vote', buttons=btns, help_text='请选择要放逐的玩家')]

    # ── Room control ──────────────────────────────────────────────────────────
    if not room.started:
        ctrl = [{'label': '离开房间', 'value': 'leave_room', 'color': 'danger'},
                {'label': '起立', 'value': 'stand_up', 'color': 'warning'}]
        ops += [_stub_actions(name='room_control', buttons=ctrl, help_text='房间操作')]

    # Night confirm button
    NIGHT_STAGES = {GameStage.HALF_BLOOD, GameStage.NIGHTMARE, GameStage.WOLF,
                    GameStage.WOLF_BEAUTY, GameStage.SEER, GameStage.WITCH,
                    GameStage.GUARD, GameStage.HUNTER, GameStage.WOLF_KING, GameStage.DREAMER,
                    GameStage.MECHANICAL_WOLF_LEARN, GameStage.MECHANICAL_WOLF_ACT,
                    GameStage.MAGIC_MIRROR_GIRL}
    if (room.stage in NIGHT_STAGES and user.role_instance and
            user.role_instance.can_act_at_night and user.role_instance.needs_global_confirm):
        ops += [_stub_actions(name='confirm_action', buttons=['确认'], help_text='确认当前选择（20秒内）')]

    return ops


def _build_seat_snapshot(room: Room) -> dict:
    return room.get_seat_snapshot()


async def push_state(user: User):
    """Push pending messages and current UI state to a single user."""
    if not user.sid:
        return
    room = user.room

    msgs = user.get_pending_messages()
    if msgs:
        await sio.emit('messages', msgs, to=user.sid)

    seat_snap = _build_seat_snapshot(room) if room else None
    stage_name = room.stage.name if room and room.stage else None
    actions = _compute_actions(user, room)

    countdown_ctx = _get_countdown_context(room) if room else (None, None, None)
    ckey, csecs, clabel = countdown_ctx

    if room and room.stage and not room.game_over:
        _maybe_start_countdown(user, room)

    await sio.emit('state', {
        'in_room': room is not None,
        'room_id': room.id if room else None,
        'room_desc': room.desc() if room else None,
        'room_config': _room_config_text(room) if room else None,
        'started': room.started if room else False,
        'game_over': room.game_over if room else False,
        'seat': user.seat,
        'stage': stage_name,
        'actions': actions,
        'seat_panel': seat_snap,
        'host_nick': room.get_host().nick if room and room.get_host() else None,
        'is_host': (user is room.get_host()) if room else False,
        'role_name': user.role_instance.name if user.role_instance else None,
        'countdown': {'key': ckey, 'seconds': csecs, 'label': clabel} if ckey else None,
    }, to=user.sid)


async def push_room_state_all(room: Room):
    """Push state to every connected player in a room."""
    for user in list(room.players.values()):
        await push_state(user)


async def broadcast_lobby():
    """Push an updated room list to all users currently in the lobby (no room)."""
    lobby_payload = {
        'rooms': build_room_info_lines(),
        'creation_sections': ROOM_CREATION_SECTIONS,
        'game_resource_links': GAME_RESOURCE_LINKS,
        'guide_links': GUIDE_LINKS,
        'dev_links': DEV_LINKS,
        'feedback_link': FEEDBACK_LINK,
    }
    for user in list(Global.users.values()):
        if not user.room and user.sid:
            await sio.emit('lobby', lobby_payload, to=user.sid)


def _get_countdown_context(room: Optional[Room]) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """Mirrors main.py's get_global_countdown_context."""
    if not room or not room.stage:
        return None, None, None

    stage = room.stage
    night_labels = {
        GameStage.NIGHTMARE: '梦魇行动',
        GameStage.HALF_BLOOD: '混血儿认亲',
        GameStage.WOLF: '狼人行动',
        GameStage.WOLF_BEAUTY: '狼美人魅惑',
        GameStage.SEER: '预言家查验',
        GameStage.WITCH: '女巫操作',
        GameStage.GUARD: '守卫行动',
        GameStage.HUNTER: '猎人阶段',
        GameStage.WOLF_KING: '狼王确认',
        GameStage.DREAMER: '摄梦人阶段',
        GameStage.NINE_TAILED_FOX: '九尾妖狐阶段',
        GameStage.MECHANICAL_WOLF_LEARN: '机械狼学习',
        GameStage.MECHANICAL_WOLF_ACT: '机械狼行动',
        GameStage.MAGIC_MIRROR_GIRL: '通灵师查验',
    }
    if stage in night_labels:
        key = f"{stage.name}_round{room.round}"
        return key, 20, f"{night_labels[stage]}倒计时"

    sheriff_state = getattr(room, 'sheriff_state', {}) or {}
    day_state = getattr(room, 'day_state', {}) or {}

    if stage == GameStage.SHERIFF:
        phase = sheriff_state.get('phase')
        if phase == 'signup':
            return f"sheriff_signup_r{room.round}", 10, '上警报名倒计时'
        if phase == 'deferred_withdraw':
            return f"sheriff_deferred_r{room.round}", 10, '退水决定倒计时'
        if phase == 'vote':
            return f"sheriff_vote_r{room.round}", 10, '警长投票倒计时'
        if phase == 'pk_vote':
            return f"sheriff_pk_vote_r{room.round}", 10, '警长PK投票倒计时'
        if day_state.get('phase') == 'await_sheriff_order':
            captain = room.skill.get('sheriff_captain') if hasattr(room, 'skill') else None
            anchor = captain or 'system'
            return f"sheriff_order_r{room.round}_{anchor}", 10, '发言顺序倒计时'

    if stage == GameStage.LAST_WORDS:
        current = day_state.get('current_last_word')
        if current:
            player = room.players.get(current)
            if player:
                if not player.skill.get('last_words_skill_resolved'):
                    return f"lastwords_skill_r{room.round}_{current}", 10, f"{current}技能抉择倒计时"
                allow = day_state.get('last_words_allow_speech', True)
                if allow and not player.skill.get('last_words_done'):
                    return f"lastwords_speech_r{room.round}_{current}", 120, f"{current}遗言倒计时"

    if stage == GameStage.SPEECH:
        speaker = getattr(room, 'current_speaker', None)
        if speaker:
            is_pk = sheriff_state.get('phase') == 'pk_speech'
            label = f"{speaker}{'警长PK发言' if is_pk else '警长竞选发言'}倒计时"
            key = f"sheriff_{'pk_' if is_pk else ''}speech_r{room.round}_{speaker}"
            return key, 120, label

    if stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
        speaker = getattr(room, 'current_speaker', None)
        if speaker:
            secs = 120
            if stage == GameStage.EXILE_SPEECH and room.skill.get('sheriff_captain') == speaker:
                secs = 150
            label_prefix = '放逐PK发言' if stage == GameStage.EXILE_PK_SPEECH else '放逐发言'
            key = f"{stage.name.lower()}_r{room.round}_{speaker}"
            return key, secs, f"{speaker}{label_prefix}倒计时"

    if stage == GameStage.BADGE_TRANSFER:
        captain = room.skill.get('sheriff_captain') if hasattr(room, 'skill') else None
        anchor = captain or day_state.get('pending_execution') or 'badge'
        return f"badge_transfer_r{room.round}_{anchor}", 10, '警徽移交倒计时'

    if stage == GameStage.EXILE_VOTE:
        return f"exile_vote_r{room.round}", 10, '放逐投票倒计时'

    if stage == GameStage.EXILE_PK_VOTE:
        return f"exile_pk_vote_r{room.round}", 10, '放逐PK投票倒计时'

    return None, None, None


# ── Countdown task (per-user) ─────────────────────────────────────────────────

NIGHT_STAGES = {GameStage.HALF_BLOOD, GameStage.NIGHTMARE, GameStage.WOLF,
                GameStage.WOLF_BEAUTY, GameStage.SEER, GameStage.WITCH,
                GameStage.GUARD, GameStage.HUNTER, GameStage.WOLF_KING, GameStage.DREAMER,
                GameStage.NINE_TAILED_FOX,
                GameStage.MECHANICAL_WOLF_LEARN, GameStage.MECHANICAL_WOLF_ACT,
                GameStage.MAGIC_MIRROR_GIRL}
DAY_TIMER_STAGES = {GameStage.SHERIFF, GameStage.LAST_WORDS, GameStage.EXILE_VOTE,
                    GameStage.EXILE_PK_VOTE, GameStage.BADGE_TRANSFER,
                    GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH, GameStage.SPEECH}
NO_WAIT_ENFORCEMENT_STAGES = {
    GameStage.SHERIFF, GameStage.LAST_WORDS, GameStage.BADGE_TRANSFER,
    GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE,
    GameStage.SPEECH, GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH,
}


async def _countdown(user: User, seconds: int):
    try:
        for i in range(seconds, 0, -1):
            if user.sid:
                await sio.emit('countdown_tick', {'seconds': i}, to=user.sid)
            await asyncio.sleep(1)

        skip = user.skill.pop('countdown_skip_timeout', False)
        if skip:
            return

        stage = getattr(user.room, 'stage', None) if user.room else None
        room_waiting = getattr(user.room, 'waiting', False) if user.room else False
        allow_without_waiting = stage in NO_WAIT_ENFORCEMENT_STAGES
        if not allow_without_waiting and not room_waiting:
            return

        try:
            if stage == GameStage.SHERIFF:
                s = getattr(user.room, 'sheriff_state', {})
                phase = s.get('phase')
                if phase == 'signup' and not user.skill.get('sheriff_voted'):
                    user.room.record_sheriff_choice(user, '不上警')
                elif phase in ('vote', 'pk_vote') and user.skill.get('sheriff_vote_pending'):
                    user.room.force_sheriff_abstain(user, reason='timeout')
                elif phase == 'deferred_withdraw':
                    user.room.complete_deferred_withdraw()
                elif (user.room.day_state.get('phase') == 'await_sheriff_order' and
                      user.nick == user.room.skill.get('sheriff_captain')):
                    user.room.force_sheriff_order_random()

            elif stage == GameStage.LAST_WORDS:
                ds = getattr(user.room, 'day_state', {})
                if ds.get('current_last_word') == user.nick:
                    if not user.skill.get('last_words_skill_resolved'):
                        user.room.handle_last_word_skill_choice(user, '放弃')
                    elif ds.get('last_words_allow_speech', True) and not user.skill.get('last_words_done'):
                        user.room.complete_last_word_speech(user)

            elif stage == GameStage.BADGE_TRANSFER:
                if (user.nick == user.room.skill.get('sheriff_captain') and
                        not user.skill.get('badge_action_taken')):
                    user.room.handle_sheriff_badge_action(user, 'destroy')

            elif stage in (GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE):
                if user.skill.get('exile_vote_pending'):
                    user.room.record_exile_vote(user, '弃票')

            elif stage == GameStage.SPEECH:
                if getattr(user.room, 'current_speaker', None) == user.nick:
                    user.room.advance_sheriff_speech(user.nick)

            elif stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
                if getattr(user.room, 'current_speaker', None) == user.nick:
                    user.room.advance_exile_speech()

            else:
                pending_keys = ['wolf_choice', 'pending_charm', 'pending_protect',
                                'pending_dream_target', 'pending_target',
                                'pending_half_blood_target', 'pending_fear',
                                'pending_learn', 'pending_act_target', 'mw_first_knife']
                has_pending = any(user.skill.get(k) for k in pending_keys)
                if (user.role_instance and user.role_instance.needs_global_confirm and
                        hasattr(user.role_instance, 'confirm')):
                    if has_pending:
                        user.role_instance.confirm()
                    else:
                        user.skip(reason='timeout')
                else:
                    user.skip(reason='timeout')

            if user.room:
                await push_room_state_all(user.room)
        except Exception as e:
            logger.warning(f'countdown timeout handler error: {e}')
    finally:
        user.skill.pop('countdown_task', None)
        user.skill.pop('countdown_stage', None)
        if user.sid:
            await sio.emit('countdown_clear', {}, to=user.sid)


def _cancel_countdown(user: User, suppress_timeout: bool = False):
    if suppress_timeout:
        user.skill['countdown_skip_timeout'] = True
    task: Optional[asyncio.Task] = user.skill.pop('countdown_task', None)
    user.skill.pop('countdown_stage', None)
    if task and not task.done():
        task.cancel()


def _maybe_start_countdown(user: User, room: Room):
    """Start a per-user countdown task if the current game state warrants one."""
    sheriff_state = getattr(room, 'sheriff_state', {}) or {}
    day_state = getattr(room, 'day_state', {}) or {}
    COUNTDOWN_STAGES = NIGHT_STAGES | DAY_TIMER_STAGES

    if room.stage not in COUNTDOWN_STAGES:
        return

    existing = user.skill.get('countdown_task')
    if existing:
        existing_stage = user.skill.get('countdown_stage')
        done = hasattr(existing, 'done') and existing.done()
        # Cancel if stage changed, task is done, or it's a speech stage and this user is no longer the speaker
        stale_speaker = (
            existing_stage in {GameStage.SPEECH, GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH}
            and existing_stage == room.stage
            and getattr(room, 'current_speaker', None) != user.nick
        )
        if done or (existing_stage is not None and existing_stage != room.stage) or stale_speaker:
            _cancel_countdown(user, suppress_timeout=True)

    if user.skill.get('countdown_task'):
        return

    should_start = False
    secs = None

    if room.stage == GameStage.SHERIFF:
        phase = sheriff_state.get('phase')
        if phase == 'signup' and not user.skill.get('sheriff_voted'):
            should_start, secs = True, 10
        elif phase in ('vote', 'pk_vote') and user.skill.get('sheriff_vote_pending'):
            should_start, secs = True, 10
        elif phase == 'deferred_withdraw':
            cands = (room.get_active_sheriff_candidates()
                     if hasattr(room, 'get_active_sheriff_candidates') else [])
            if user.nick in cands and not user.skill.get('sheriff_withdrawn'):
                should_start, secs = True, 10
        elif (day_state.get('phase') == 'await_sheriff_order' and
              room.skill.get('sheriff_captain') == user.nick):
            should_start, secs = True, 10

    elif room.stage == GameStage.LAST_WORDS:
        if day_state.get('current_last_word') == user.nick:
            allow = day_state.get('last_words_allow_speech', True)
            if not user.skill.get('last_words_skill_resolved'):
                should_start, secs = True, 10
            elif allow and not user.skill.get('last_words_done'):
                should_start, secs = True, 120

    elif room.stage == GameStage.BADGE_TRANSFER:
        if (room.skill.get('sheriff_captain') == user.nick and
                not user.skill.get('badge_action_taken')):
            should_start, secs = True, 10

    elif room.stage in (GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE):
        if user.skill.get('exile_vote_pending'):
            should_start, secs = True, 10

    elif room.stage == GameStage.SPEECH:
        if getattr(room, 'current_speaker', None) == user.nick:
            should_start, secs = True, 120

    elif room.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
        if getattr(room, 'current_speaker', None) == user.nick:
            should_start = True
            secs = 150 if (room.stage == GameStage.EXILE_SPEECH and
                           room.skill.get('sheriff_captain') == user.nick) else 120

    elif user.role_instance and user.role_instance.can_act_at_night:
        should_start, secs = True, 20

    if should_start and secs:
        if user.skill.get('countdown_skip_timeout') is not True:
            user.skill['countdown_skip_timeout'] = False
        task = asyncio.create_task(_countdown(user, secs))
        user.skill['countdown_task'] = task
        user.skill['countdown_stage'] = room.stage


# ── Action dispatch ───────────────────────────────────────────────────────────

async def _dispatch_action(user: User, room: Room, data: dict):
    """Process a player action dict and mutate game state."""
    # Play again: host triggers
    if data.get('play_again') == '再来一局' and room and room.game_over and user is room.get_host():
        if not room.skill.get('play_again_pending'):
            room.skill['play_again_pending'] = True
            room.broadcast_msg('=' * 22)
            room.broadcast_msg('房主发起再来一局！请选择是否参加新局。')
        return

    # Play again: player responds
    if data.get('play_again_response') and room and room.game_over and room.skill.get('play_again_pending'):
        response = data['play_again_response']
        if not user.skill.get('play_again_response'):
            user.skill['play_again_response'] = response
            label = '加入新局' if response == '加入' else '不参加'
            room.broadcast_msg(f'【{user.nick}】选择：{label}')
        if all(u.skill.get('play_again_response') for u in room.players.values()):
            await _reset_room_for_new_game(room)
        return

    sheriff_state = getattr(room, 'sheriff_state', {}) or {}

    # Room control
    control = data.get('room_control')
    if control == 'leave_room':
        _cancel_countdown(user, suppress_timeout=True)
        try:
            room.remove_player(user)
        except Exception:
            pass
        await push_state(user)
        return
    if control == 'stand_up':
        _cancel_countdown(user, suppress_timeout=True)
        room.release_seat(user)
        return

    # Host: room config modal
    if data.get('host_op') == '房间配置' and room and user is room.get_host() and not room.started:
        await sio.emit('configure_room_modal', _room_config_dict(room), to=user.sid)
        return

    # Host: sheriff vote
    if data.get('sheriff_host_action') and user is room.get_host():
        action = data['sheriff_host_action']
        if action == '警长投票':
            msg = room.start_sheriff_vote(pk_mode=False)
            if msg:
                user.send_msg(msg)
        elif action == '警长PK投票':
            msg = room.start_sheriff_vote(pk_mode=True)
            if msg:
                user.send_msg(msg)

    # Host: day actions
    if data.get('day_host_action') and user is room.get_host():
        action = data['day_host_action']
        if action == '公布昨夜信息':
            msg = await room.publish_night_info()
            if msg:
                user.send_msg(msg)
        elif action == '放逐投票':
            msg = room.start_exile_vote(pk_mode=False)
            if msg:
                user.send_msg(msg)
        elif action == '放逐PK投票':
            msg = room.start_exile_vote(pk_mode=True)
            if msg:
                user.send_msg(msg)

    # Night role confirm button
    if data.get('confirm_action') and user.role_instance:
        NIGHT_STAGES_ = NIGHT_STAGES
        preserve = (room.stage in NIGHT_STAGES_ and
                    user.role_instance.can_act_at_night)
        if not preserve:
            _cancel_countdown(user, suppress_timeout=True)
        if hasattr(user.role_instance, 'confirm'):
            try:
                user.role_instance.confirm()
            except Exception as e:
                user.send_msg(f'确认失败: {e}')
        return  # skip further processing

    # Role input handlers
    if user.role_instance:
        user.role_instance.handle_inputs(data)

    # Sheriff signup
    if data.get('sheriff_vote'):
        room.record_sheriff_choice(user, data['sheriff_vote'])
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('sheriff_withdraw'):
        msg = room.handle_sheriff_withdraw(user)
        if msg:
            user.send_msg(msg)

    if data.get('sheriff_ballot'):
        selection = data['sheriff_ballot']
        target = '弃票' if selection == '弃票' else selection.split('.', 1)[-1].strip()
        room.record_sheriff_ballot(user, target)
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('sheriff_set_order'):
        msg = room.set_sheriff_order(user, data['sheriff_set_order'])
        if msg:
            user.send_msg(msg)

    if data.get('last_word_skill'):
        room.handle_last_word_skill_choice(user, data['last_word_skill'])
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('last_word_done'):
        room.complete_last_word_speech(user)
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('idiot_badge_transfer'):
        msg = room.handle_idiot_badge_transfer(user, data['idiot_badge_transfer'])
        if msg:
            user.send_msg(msg)

    if data.get('sheriff_badge_action'):
        msg = room.handle_sheriff_badge_action(user, data['sheriff_badge_action'])
        if msg:
            user.send_msg(msg)
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('exile_vote'):
        selection = data['exile_vote']
        target = '弃票' if selection == '弃票' else selection.split('.', 1)[-1].strip()
        room.record_exile_vote(user, target)
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('wolf_self_bomb'):
        msg = room.handle_wolf_self_bomb(user)
        if msg:
            user.send_msg(msg)
        _cancel_countdown(user, suppress_timeout=True)

    if data.get('speech_done') and user.nick == getattr(room, 'current_speaker', None):
        _cancel_countdown(user, suppress_timeout=True)
        user.skip(reason='speech_done')
        if room.stage == GameStage.SPEECH:
            room.advance_sheriff_speech(user.nick)
        elif room.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
            room.advance_exile_speech()


# ── Socket.IO event handlers ──────────────────────────────────────────────────

@sio.on('connect')
async def on_connect(sid, environ, auth):
    token = (auth or {}).get('token', '')
    nick = _token_to_nick.get(token)
    if nick and nick in Global.users:
        user = Global.users[nick]
        # Unbind old sid if still registered
        if user.sid and user.sid != sid:
            _sid_to_nick.pop(user.sid, None)
        user.sid = sid
        _sid_to_nick[sid] = nick
        logger.info(f'用户 "{nick}" 重连 sid={sid}')
        await push_state(user)
        # Restart countdown if needed
        if user.room:
            _maybe_start_countdown(user, user.room)
    else:
        # New connection – wait for 'login' event
        logger.info(f'新连接 sid={sid}')


@sio.on('disconnect')
async def on_disconnect(sid):
    nick = _sid_to_nick.pop(sid, None)
    if nick and nick in Global.users:
        user = Global.users[nick]
        user.sid = None
        logger.info(f'用户 "{nick}" 断开连接')


@sio.on('login')
async def on_login(sid, data):
    nick = (data.get('nick') or '').strip()
    err = User.validate_nick(nick)
    if err:
        await sio.emit('login_error', {'message': err}, to=sid)
        return
    token = secrets.token_urlsafe(16)
    user = User.alloc(nick, sid, token)
    _token_to_nick[token] = nick
    _sid_to_nick[sid] = nick
    await sio.emit('login_ok', {'token': token, 'nick': nick}, to=sid)
    await sio.emit('lobby', {
        'rooms': build_room_info_lines(),
        'creation_sections': ROOM_CREATION_SECTIONS,
        'game_resource_links': GAME_RESOURCE_LINKS,
        'guide_links': GUIDE_LINKS,
        'dev_links': DEV_LINKS,
        'feedback_link': FEEDBACK_LINK,
    }, to=sid)


@sio.on('get_lobby')
async def on_get_lobby(sid, data):
    user = _user_by_sid(sid)
    if not user:
        return
    await sio.emit('lobby', {
        'rooms': build_room_info_lines(),
        'creation_sections': ROOM_CREATION_SECTIONS,
        'game_resource_links': GAME_RESOURCE_LINKS,
        'guide_links': GUIDE_LINKS,
        'dev_links': DEV_LINKS,
        'feedback_link': FEEDBACK_LINK,
    }, to=sid)


@sio.on('create_room')
async def on_create_room(sid, data):
    user = _user_by_sid(sid)
    if not user or user.room:
        return
    preset = data.get('preset', PRESET_CUSTOM)
    custom = data.get('custom')
    config = resolve_room_config(preset, custom)
    if not config:
        await sio.emit('error', {'message': '无效的房间配置'}, to=sid)
        return

    roles = build_roles_from_config(config)
    if not roles:
        await sio.emit('error', {'message': '角色配置为空'}, to=sid)
        return

    room = Room.alloc(config)
    room.add_player(user)
    user.send_msg(f'房间配置：{_room_config_text(room)}')
    await push_state(user)
    await broadcast_lobby()


@sio.on('join_room')
async def on_join_room(sid, data):
    user = _user_by_sid(sid)
    if not user or user.room:
        return
    room_id = str(data.get('room_id', '')).strip()
    room = Room.get(room_id)
    if not room:
        await sio.emit('error', {'message': '房间不存在或已关闭'}, to=sid)
        return
    try:
        room.add_player(user)
    except (ValueError, AssertionError) as e:
        await sio.emit('error', {'message': str(e)}, to=sid)
        return
    user.send_msg(f'房间配置：{_room_config_text(room)}')
    await push_room_state_all(room)
    await broadcast_lobby()


@sio.on('select_seat')
async def on_select_seat(sid, data):
    user = _user_by_sid(sid)
    if not user or not user.room:
        return
    room = user.room
    if room.started:
        await sio.emit('error', {'message': '游戏已开始，座位不可更换'}, to=sid)
        return
    seat = data.get('seat')
    try:
        seat = int(seat)
    except (TypeError, ValueError):
        await sio.emit('error', {'message': '无效座位'}, to=sid)
        return
    if seat == user.seat:
        return
    room.release_seat(user)
    try:
        room.assign_seat(user, seat)
    except ValueError as e:
        # Try to restore original seat or leave standing
        await sio.emit('error', {'message': str(e)}, to=sid)
    user.send_msg(f'你当前的号码牌：{user.seat}号')
    await push_room_state_all(room)


@sio.on('configure_room')
async def on_configure_room(sid, data):
    """Host updates room config before game start."""
    user = _user_by_sid(sid)
    if not user or not user.room or user is not user.room.get_host():
        return
    room = user.room
    if room.started:
        return
    config = data.get('config', {})
    roles = build_roles_from_config(config)
    if not roles:
        await sio.emit('error', {'message': '角色配置为空'}, to=sid)
        return
    room.roles = list(roles)
    room.roles_pool = list(roles)
    room.witch_rule = WitchRule.from_option(
        config.get('witch_rule', DEFAULT_ROOM_RULES['witch_rule']))
    room.guard_rule = GuardRule.from_option(
        config.get('guard_rule', DEFAULT_ROOM_RULES['guard_rule']))
    room.sheriff_bomb_rule = SheriffBombRule.from_option(
        config.get('sheriff_bomb_rule', DEFAULT_ROOM_RULES['sheriff_bomb_rule']))
    if Role.MECHANICAL_WOLF in room.roles:
        room.mw_shield_blocks_hunter = bool(config.get('mw_shield_blocks_hunter', False))
        room.mw_double_knife_breaks_shield = bool(config.get('mw_double_knife_breaks_shield', False))
    else:
        room.mw_shield_blocks_hunter = False
        room.mw_double_knife_breaks_shield = False
    room._mark_seat_state_dirty()
    room.broadcast_msg(f'房间配置已更新：{room.desc()}')
    await push_room_state_all(room)


@sio.on('player_action')
async def on_player_action(sid, data):
    user = _user_by_sid(sid)
    if not user:
        return
    room = user.room

    # '开始游戏' runs asynchronously so the response is immediate
    if data.get('host_op') == '开始游戏':
        if room and user is room.get_host():
            if room.started:
                user.send_msg('游戏已经开始')
            elif len(room.players) < len(room.roles):
                user.send_msg(f'需要 {len(room.roles)} 名玩家才能开始，当前只有 {len(room.players)} 人')
            else:
                asyncio.create_task(_run_start_game(room))
        if room:
            await push_room_state_all(room)
        else:
            await push_state(user)
        return

    await _dispatch_action(user, room if room else None, data)

    # Start per-user countdown if appropriate
    if room and room.stage and not room.game_over:
        _maybe_start_countdown(user, room)

    if room:
        await push_room_state_all(room)
    else:
        await push_state(user)


@sio.on('leave_room')
async def on_leave_room(sid, data):
    user = _user_by_sid(sid)
    if not user or not user.room:
        return
    room = user.room
    _cancel_countdown(user, suppress_timeout=True)
    try:
        room.remove_player(user)
    except Exception:
        pass
    await push_state(user)
    if room.players:
        await push_room_state_all(room)
    await broadcast_lobby()


@sio.on('logout')
async def on_logout(sid, data):
    user = _user_by_sid(sid)
    if not user:
        return
    _cancel_countdown(user, suppress_timeout=True)
    _token_to_nick.pop(user.reconnect_token, None)
    _sid_to_nick.pop(sid, None)
    User.free(user)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    ip = get_interface_ip()

    # Optional ngrok
    ngrok_url = None
    if os.environ.get('DISABLE_NGROK', '').lower() not in ('1', 'true', 'yes'):
        try:
            from pyngrok import ngrok as _ngrok
            if os.environ.get('NGROK_AUTHTOKEN') or os.environ.get('NGROK_AUTH_TOKEN'):
                public = _ngrok.connect(port, bind_tls=True)
                ngrok_url = str(public).replace('NgrokTunnel: "', '').replace('"', '')
        except Exception as e:
            logger.warning(f'ngrok 启动失败：{e}')

    print('\n' + '=' * 70)
    print('       -Moon Verdict 月光审判- 狼人杀已上线！')
    print(f'       局域网地址 → http://{ip}:{port}')
    if ngrok_url:
        print(f'       公网地址   → {ngrok_url}')
    print('=' * 70 + '\n')

    uvicorn.run(app, host='0.0.0.0', port=port, access_log=False)
