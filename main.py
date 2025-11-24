# main.py
import asyncio
import sys
import platform
import signal
import re
import json
import html
from typing import Optional, Tuple
from logging import getLogger, basicConfig

from pywebio import start_server
from pywebio.platform.tornado import ioloop as get_pywebio_ioloop
from pywebio.input import *
from pywebio.output import *
from pywebio.output import use_scope
from pywebio.session import defer_call, get_current_task_id, get_current_session, set_env


from enums import WitchRule, GuardRule, SheriffBombRule, Role, GameStage, PlayerStatus
from models.room import Room
from models.user import User
from utils import add_cancel_button, get_interface_ip

# ==================== 接入外网：pyngrok ====================
from pyngrok import ngrok
import threading
import os

basicConfig(stream=sys.stdout,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = getLogger('Wolf')
logger.setLevel('DEBUG')


def make_scope_name(prefix: str, nick: str) -> str:
    """Sanitize nicknames for PyWebIO scope names."""
    suffix = re.sub(r'[^0-9A-Za-z_-]', '_', nick)
    if not suffix:
        suffix = 'player'
    return f'{prefix}_{suffix}'


def build_page_title(room: Room, user: User) -> str:
    room_id = room.id if room and room.id is not None else '未分配'
    seat = user.seat if user.seat is not None else '?'
    parts = [f'房间:{room_id}', f'{seat}号', user.nick]
    if room and room.started and user.role_instance:
        parts.append(user.role_instance.name)
    return '｜'.join(parts)


def format_player_label(room: Room, nick: str) -> str:
    player = room.players.get(nick) if room else None
    seat = player.seat if player and player.seat is not None else '?'
    return f"{seat}号{nick}"


def get_global_countdown_context(room: Optional[Room]) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    if not room or not room.stage:
        return None, None, None

    stage = room.stage
    night_labels = {
        GameStage.WOLF: '狼人行动',
        GameStage.SEER: '预言家查验',
        GameStage.WITCH: '女巫操作',
        GameStage.GUARD: '守卫行动',
        GameStage.HUNTER: '猎人阶段',
        GameStage.WOLF_KING: '狼王确认',
        GameStage.DREAMER: '摄梦人阶段',
    }
    if stage in night_labels:
        label = f"{night_labels[stage]}倒计时"
        key = f"{stage.name}_round{room.round}"
        return key, 20, label

    sheriff_state = getattr(room, 'sheriff_state', {}) or {}
    day_state = getattr(room, 'day_state', {}) or {}

    if stage == GameStage.SHERIFF:
        phase = sheriff_state.get('phase')
        if phase == 'signup':
            key = f"sheriff_signup_r{room.round}"
            return key, 10, '上警报名倒计时'
        if phase == 'deferred_withdraw':
            key = f"sheriff_deferred_r{room.round}"
            return key, 10, '退水决定倒计时'
        if phase == 'vote':
            key = f"sheriff_vote_r{room.round}"
            return key, 10, '警长投票倒计时'
        if phase == 'pk_vote':
            key = f"sheriff_pk_vote_r{room.round}"
            return key, 10, '警长PK投票倒计时'
        if day_state.get('phase') == 'await_sheriff_order':
            captain = room.skill.get('sheriff_captain') if hasattr(room, 'skill') else None
            anchor = captain or 'system'
            key = f"sheriff_order_r{room.round}_{anchor}"
            return key, 10, '发言顺序倒计时'

    if stage == GameStage.LAST_WORDS:
        current = day_state.get('current_last_word')
        if current:
            player = room.players.get(current)
            if player:
                allow_speech = day_state.get('last_words_allow_speech', True)
                if not player.skill.get('last_words_skill_resolved', False):
                    key = f"lastwords_skill_r{room.round}_{current}"
                    label = f"{format_player_label(room, current)}技能抉择倒计时"
                    return key, 10, label
                if allow_speech and not player.skill.get('last_words_done', False):
                    key = f"lastwords_speech_r{room.round}_{current}"
                    label = f"{format_player_label(room, current)}遗言倒计时"
                    return key, 120, label

    if stage == GameStage.SPEECH:
        speaker = getattr(room, 'current_speaker', None)
        if speaker:
            phase = sheriff_state.get('phase')
            is_pk = phase == 'pk_speech'
            label = f"{format_player_label(room, speaker)}{'警长PK发言' if is_pk else '警长竞选发言'}倒计时"
            key = f"sheriff_{'pk_' if is_pk else ''}speech_r{room.round}_{speaker}"
            return key, 120, label

    if stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
        speaker = getattr(room, 'current_speaker', None)
        if speaker:
            seconds = 120
            if stage == GameStage.EXILE_SPEECH and room.skill.get('sheriff_captain') == speaker:
                seconds = 150
            label_prefix = '放逐PK发言' if stage == GameStage.EXILE_PK_SPEECH else '放逐发言'
            label = f"{format_player_label(room, speaker)}{label_prefix}倒计时"
            key = f"{stage.name.lower()}_r{room.round}_{speaker}"
            return key, seconds, label

    if stage == GameStage.BADGE_TRANSFER:
        captain = room.skill.get('sheriff_captain') if hasattr(room, 'skill') else None
        anchor = captain or day_state.get('pending_execution') or 'badge'
        key = f"badge_transfer_r{room.round}_{anchor}"
        return key, 10, '警徽移交倒计时'

    if stage == GameStage.EXILE_VOTE:
        key = f"exile_vote_r{room.round}"
        return key, 10, '放逐投票倒计时'

    if stage == GameStage.EXILE_PK_VOTE:
        key = f"exile_pk_vote_r{room.round}"
        return key, 10, '放逐PK投票倒计时'

    return None, None, None


GLOBAL_COUNTDOWN_READY_HTML = """
<div style='margin:8px 0;font-size:26px;font-weight:bold;color:#c00;'>倒计时：准备中</div>
<script>
(function() {
    if (!window.__globalTimerIntervals) return;
    Object.keys(window.__globalTimerIntervals).forEach(function(k) {
        clearInterval(window.__globalTimerIntervals[k]);
        delete window.__globalTimerIntervals[k];
    });
})();
</script>
"""


def make_dom_id(prefix: str, key: str) -> str:
        safe = re.sub(r'[^0-9A-Za-z_-]', '_', key) or 'timer'
        return f"{prefix}_{safe}"


def build_js_countdown_html(label: str, seconds: int, key: str) -> str:
        container_id = make_dom_id('global_timer', key)
        seconds_json = json.dumps(seconds)
        label_html = html.escape(label)
        return f"""
<div id='{container_id}' style='margin:8px 0;font-size:26px;font-weight:bold;color:#c00;'>
    <span class='global-countdown-label'>{label_html}</span>：<span class='global-countdown-value'>{seconds}s</span>
</div>
<script>
(function() {{
    var container = document.getElementById('{container_id}');
    if (!container) return;
    window.__globalTimerIntervals = window.__globalTimerIntervals || {{}};
    Object.keys(window.__globalTimerIntervals).forEach(function(k) {{
        clearInterval(window.__globalTimerIntervals[k]);
        delete window.__globalTimerIntervals[k];
    }});
    var valueEl = container.querySelector('.global-countdown-value');
    if (!valueEl) return;
    var remaining = {seconds_json};
    function render(val) {{ valueEl.textContent = val; }}
    render(remaining + 's');
    var timer = setInterval(function() {{
        remaining -= 1;
        if (remaining <= 0) {{
            render('已结束');
            clearInterval(timer);
            delete window.__globalTimerIntervals['{container_id}'];
            return;
        }}
        render(remaining + 's');
    }}, 1000);
    window.__globalTimerIntervals['{container_id}'] = timer;
}})();
</script>
"""


async def main():
    set_env(title="Moon Verdict 狼人杀法官助手")
    put_markdown("## 狼人杀法官")
    current_user = User.alloc(
        await input('请输入你的昵称',
                    required=True,
                    validate=User.validate_nick,
                    help_text='请使用一个易于分辨的名称'),
        get_current_task_id()
    )
    welcome_title = f"Moon Verdict： 欢迎{current_user.nick}加入游戏"
    set_env(title=welcome_title)

    @defer_call
    def on_close():
        User.free(current_user)
        task = current_user.skill.pop('countdown_task', None)
        if task:
            task.cancel()
        current_user.skill.pop('global_display_seen_key', None)
        current_user.skill.pop('global_display_idle', None)

    put_text(f'你好，{current_user.nick}')
    data = await input_group(
        '大厅', inputs=[actions(name='cmd', buttons=['创建房间', '加入房间'])]
    )

    if data['cmd'] == '创建房间':
        # 先显示板子预设选择
        preset_data = await input_group('板子预设', inputs=[
            actions(
                name='preset',
                buttons=['3人测试板子', '预女猎守1狼6人测试', '预女猎守2狼7人测试', '自定义配置'],
                help_text='选择预设或自定义'
            )
        ])
        
        if preset_data['preset'] == '3人测试板子':
            # 使用3人测试板子预设：1普通狼人，1平民，1预言家
            room_config = {
                'wolf_num': 1,
                'god_wolf': [],
                'citizen_num': 1,
                'god_citizen': ['预言家'],
                'witch_rule': '仅第一夜可自救',
                'guard_rule': '同时被守被救时，对象死亡',
                'sheriff_bomb_rule': '双爆吞警徽'
            }
        elif preset_data['preset'] == '预女猎守1狼6人测试':
            room_config = {
                'wolf_num': 1,
                'god_wolf': [],
                'citizen_num': 1,
                'god_citizen': ['预言家', '女巫', '守卫', '猎人'],
                'witch_rule': '仅第一夜可自救',
                'guard_rule': '同时被守被救时，对象死亡',
                'sheriff_bomb_rule': '双爆吞警徽'
            }
        elif preset_data['preset'] == '预女猎守2狼7人测试':
            room_config = {
                'wolf_num': 2,
                'god_wolf': [],
                'citizen_num': 1,
                'god_citizen': ['预言家', '女巫', '守卫', '猎人'],
                'witch_rule': '仅第一夜可自救',
                'guard_rule': '同时被守被救时，对象死亡',
                'sheriff_bomb_rule': '双爆吞警徽'
            }
        else:
            # 自定义配置
            room_config = await input_group('房间设置', inputs=[
                input(name='wolf_num', label='普通狼数', type=NUMBER, value='3'),
                checkbox(name='god_wolf', label='特殊狼', inline=True, options=Role.as_god_wolf_options()),
                input(name='citizen_num', label='普通村民数', type=NUMBER, value='4'),
                checkbox(name='god_citizen', label='特殊村民', inline=True,
                         options=Role.as_god_citizen_options()),
                select(name='witch_rule', label='女巫解药规则', options=WitchRule.as_options()),
                select(name='guard_rule', label='守卫规则', options=GuardRule.as_options()),
                select(name='sheriff_bomb_rule', label='自曝警徽规则', options=SheriffBombRule.as_options(), value=SheriffBombRule.DOUBLE_LOSS.value),
            ])
        room = Room.alloc(room_config)
    elif data['cmd'] == '加入房间':
        room = Room.get(await input('房间号', type=TEXT, validate=Room.validate_room_join))
    else:
        raise NotImplementedError

    # 增大消息显示区域高度，提供更充足的聊天/系统信息显示空间
    put_scrollable(current_user.game_msg, height=600, keep_bottom=True)
    current_user.game_msg.append(put_text(room.desc()))

    room.add_player(current_user)

    with use_scope(make_scope_name('global_countdown', current_user.nick), clear=True):
        put_html(GLOBAL_COUNTDOWN_READY_HTML)

    def trigger_manual_refresh():
        task = current_user.skill.pop('countdown_task', None)
        if task:
            task.cancel()
        current_user.skill.pop('global_display_seen_key', None)
        current_user.skill.pop('global_display_idle', None)
        try:
            get_current_session().send_client_event({
                'event': 'from_cancel',
                'task_id': current_user.main_task_id,
                'data': None
            })
        except Exception:
            pass

    with use_scope(make_scope_name('global_controls', current_user.nick), clear=True):
        put_buttons(
            [
                {
                    'label': '刷新操作窗口',
                    'value': 'manual_refresh',
                    'color': 'success'
                }
            ],
            onclick=lambda _: trigger_manual_refresh()
        )

    last_title = welcome_title

    while True:
        try:
            await asyncio.sleep(0.2)
        except (RuntimeError, asyncio.CancelledError):
            # Refreshing the PyWebIO page may cancel the pending sleep; ignore and continue
            continue

        try:
            new_title = build_page_title(room, current_user)
            if new_title != last_title:
                set_env(title=new_title)
                last_title = new_title
        except Exception:
            pass

        try:
            display_key, display_seconds, display_label = get_global_countdown_context(room)
            seen_key = current_user.skill.get('global_display_seen_key')
            is_idle = current_user.skill.get('global_display_idle', False)
            scope_name = make_scope_name('global_countdown', current_user.nick)
            if not display_key or not display_seconds or not display_label:
                if seen_key is not None or not is_idle:
                    with use_scope(scope_name, clear=True):
                        put_html(GLOBAL_COUNTDOWN_READY_HTML)
                current_user.skill.pop('global_display_seen_key', None)
                current_user.skill['global_display_idle'] = True
            else:
                current_user.skill['global_display_idle'] = False
                if seen_key != display_key:
                    html = build_js_countdown_html(display_label, display_seconds, display_key)
                    with use_scope(scope_name, clear=True):
                        put_html(html)
                    current_user.skill['global_display_seen_key'] = display_key
        except Exception:
            pass

        # 非夜晚房主操作
        host_ops = []
        sheriff_state = getattr(room, 'sheriff_state', {})
        day_state = getattr(room, 'day_state', {})
        if current_user is room.get_host():
            if not room.started:
                host_ops += [
                    actions(name='host_op', buttons=['开始游戏', '房间配置'], help_text='你是房主')
                ]
            if room.stage in (GameStage.SHERIFF, GameStage.SPEECH):
                if sheriff_state.get('phase') == 'await_vote':
                    host_ops += [
                        actions(
                            name='sheriff_host_action',
                            buttons=['警长投票'],
                            help_text='发起警长投票'
                        )
                    ]
                elif sheriff_state.get('phase') == 'await_pk_vote':
                    host_ops += [
                        actions(
                            name='sheriff_host_action',
                            buttons=['警长PK投票'],
                            help_text='发起警长PK投票'
                        )
                    ]
            if day_state.get('phase') == 'announcement':
                host_ops += [
                    actions(
                        name='day_host_action',
                        buttons=['公布昨夜信息'],
                        help_text='公布昨夜死亡情况'
                    )
                ]
            elif day_state.get('phase') == 'await_exile_vote':
                host_ops += [
                    actions(
                        name='day_host_action',
                        buttons=['放逐投票'],
                        help_text='发起放逐投票'
                    )
                ]
            elif day_state.get('phase') == 'await_exile_pk_vote':
                host_ops += [
                    actions(
                        name='day_host_action',
                        buttons=['放逐PK投票'],
                        help_text='发起放逐PK投票'
                    )
                ]

        # 玩家操作
        user_ops = []
        if room.started and current_user.role_instance:
            user_ops = current_user.role_instance.get_actions()

            # === 警长竞选阶段 ===
            can_join_sheriff = False
            if hasattr(room, 'can_participate_in_sheriff'):
                can_join_sheriff = room.can_participate_in_sheriff(current_user.nick)
            else:
                can_join_sheriff = current_user.status == PlayerStatus.ALIVE

            if room.stage in (GameStage.SHERIFF, GameStage.SPEECH) and can_join_sheriff:
                state_phase = sheriff_state.get('phase')
                if state_phase == 'signup' and not current_user.skill.get('sheriff_voted', False):
                    user_ops += [
                        actions(
                            name='sheriff_vote',
                            buttons=['上警', '不上警'],
                            help_text='请选择是否上警（10秒内未选则视为不上警）'
                        )
                    ]

                active_candidates = room.get_active_sheriff_candidates() if hasattr(room, 'get_active_sheriff_candidates') else []
                if (
                    state_phase in ('speech', 'await_vote', 'pk_speech', 'await_pk_vote') and
                    current_user.nick in active_candidates and
                    not current_user.skill.get('sheriff_withdrawn', False)
                ):
                    user_ops += [
                        actions(
                            name='sheriff_withdraw',
                            buttons=['退水'],
                            help_text='退水后将退出竞选'
                        )
                    ]

                if (
                    state_phase in ('vote', 'pk_vote') and
                    current_user.nick in sheriff_state.get('eligible_voters', []) and
                    not current_user.skill.get('sheriff_has_balloted', False)
                ):
                    buttons = []
                    candidates = active_candidates
                    for nick in candidates:
                        player_obj = room.players.get(nick)
                        seat = player_obj.seat if player_obj and player_obj.seat is not None else '?'
                        buttons.append({'label': f"{seat}. {nick}", 'value': f"{seat}. {nick}"})
                    buttons.append({'label': '弃票', 'value': '弃票', 'color': 'secondary'})
                    help_text = '请选择支持的警长候选人'
                    user_ops += [
                        actions(
                            name='sheriff_ballot',
                            buttons=buttons,
                            help_text=help_text
                        )
                    ]

            # === 遗言/技能阶段 ===
            if room.stage == GameStage.LAST_WORDS and day_state.get('current_last_word') == current_user.nick:
                supports_skill = bool(current_user.role_instance and hasattr(current_user.role_instance, 'supports_last_skill') and current_user.role_instance.supports_last_skill())
                if (not current_user.skill.get('last_words_skill_resolved', False)) and not current_user.skill.get('pending_last_skill', False):
                    buttons = ['放弃']
                    if supports_skill:
                        can_trigger_skill = True
                        if current_user.role in (Role.HUNTER, Role.WOLF_KING) and not current_user.skill.get('can_shoot', True):
                            can_trigger_skill = False
                        if can_trigger_skill:
                            buttons = ['发动技能', '放弃']
                        else:
                            buttons = [
                                {
                                    'label': '发动技能（不可用）',
                                    'value': 'disabled_last_skill',
                                    'disabled': True,
                                    'color': 'secondary'
                                },
                                '放弃'
                            ]
                    user_ops += [
                        actions(
                            name='last_word_skill',
                            buttons=buttons,
                            help_text='发表遗言前是否发动技能？（10秒）'
                        )
                    ]
                elif day_state.get('last_words_allow_speech', True) and not current_user.skill.get('last_words_done', False):
                    user_ops += [
                        actions(
                            name='last_word_done',
                            buttons=['遗言结束'],
                            help_text='发表完遗言后点击'
                        )
                    ]

            # === 警长选择发言顺序 ===
            if (
                day_state.get('phase') == 'await_sheriff_order' and
                room.skill.get('sheriff_captain') == current_user.nick and
                current_user.status == PlayerStatus.ALIVE
            ):
                user_ops += [
                    actions(
                        name='sheriff_set_order',
                        buttons=['顺序发言', '逆序发言'],
                        help_text='请选择今日发言顺序'
                    )
                ]
            if current_user.skill.get('idiot_badge_transfer_required'):
                candidates = [
                    u for u in room.list_alive_players()
                    if u.nick != current_user.nick
                ]
                buttons = []
                for player in candidates:
                    seat = player.seat if player.seat is not None else '?'
                    buttons.append({'label': f'交给{seat}号{player.nick}', 'value': player.nick})
                if not buttons:
                    buttons.append({'label': '无人可交出，空缺', 'value': 'forfeit', 'color': 'warning'})
                user_ops += [
                    actions(
                        name='idiot_badge_transfer',
                        buttons=buttons,
                        help_text='白痴必须立即移交警徽'
                    )
                ]

            if (
                room.stage == GameStage.BADGE_TRANSFER and
                room.skill.get('sheriff_captain') == current_user.nick and
                not current_user.skill.get('badge_action_taken', False)
            ):
                alive_players = [u for u in room.list_alive_players() if u.nick != current_user.nick]
                badge_buttons = []
                for p in alive_players:
                    seat = p.seat if p.seat is not None else '?'
                    badge_buttons.append({'label': f'交给{seat}号{p.nick}', 'value': f'transfer:{p.nick}'})
                badge_buttons.append({'label': '撕毁警徽', 'value': 'destroy', 'color': 'danger'})
                user_ops += [
                    actions(
                        name='sheriff_badge_action',
                        buttons=badge_buttons,
                        help_text='请选择移交对象或撕毁警徽（10秒）'
                    )
                ]


            if room.can_wolf_self_bomb(current_user):
                user_ops += [
                    actions(
                        name='wolf_self_bomb',
                        buttons=[{'label': '自曝', 'value': 'boom', 'color': 'danger'}],
                        help_text='立即结束当前阶段并出局'
                    )
                ]

            # === 发言阶段 ===
            if (
                hasattr(room, 'current_speaker') and
                room.stage in (GameStage.SPEECH, GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH) and
                current_user.nick == room.current_speaker
            ):
                user_ops += [
                    actions(
                        name='speech_done',
                        buttons=['发言完毕'],
                        help_text='点击结束发言'
                    )
                ]

            # === 放逐投票 ===
            if room.stage in (GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE):
                if current_user.skill.get('exile_vote_pending', False):
                    buttons = []
                    candidates = day_state.get('vote_candidates', [])
                    for nick in candidates:
                        player_obj = room.players.get(nick)
                        seat = player_obj.seat if player_obj and player_obj.seat is not None else '?'
                        buttons.append({'label': f"{seat}. {nick}", 'value': f"{seat}. {nick}"})
                    buttons.append({'label': '弃票', 'value': '弃票', 'color': 'secondary'})
                    user_ops += [
                        actions(
                            name='exile_vote',
                            buttons=buttons,
                            help_text='请选择要放逐的玩家'
                        )
                    ]

        ops = host_ops + user_ops
        if not ops:
            continue

        if ops:
            NIGHT_STAGES = {GameStage.WOLF, GameStage.SEER, GameStage.WITCH, GameStage.GUARD, GameStage.HUNTER, GameStage.WOLF_KING, GameStage.DREAMER}
            # 夜间操作显示 20s 倒计时与确认键
            if room.stage is not None:
                # 仅在有玩家操作时（夜晚阶段）追加确认键
                # 避免重复添加：只在 user_ops 非空且为夜间角色时加入确认
                try:
                    if (
                        room.stage in NIGHT_STAGES and
                        current_user.role_instance and
                        current_user.role_instance.can_act_at_night and
                        current_user.role_instance.needs_global_confirm
                    ):
                        ops = ops + [actions(name='confirm_action', buttons=['确认'], help_text='确认当前选择（20秒内）')]
                except Exception:
                    pass

            # 开启倒计时任务（每个玩家单独）仅在夜间角色可行动时启动
            DAY_TIMER_STAGES = {GameStage.SHERIFF, GameStage.LAST_WORDS, GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE, GameStage.BADGE_TRANSFER, GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH, GameStage.SPEECH}
            COUNTDOWN_STAGES = NIGHT_STAGES | DAY_TIMER_STAGES
            
            async def _countdown(user, seconds=20):
                try:
                    for i in range(seconds, 0, -1):
                        # 调试日志（不再发送到玩家私聊或终端），仅在 logger 中记录
                        # 不在终端或私聊输出调试信息，避免污染日志/消息区

                        # 在操作窗口内的专用 scope 中更新倒计时（覆盖同一行），避免消息区污染
                        try:
                            with use_scope(make_scope_name('input_countdown', user.nick), clear=True):
                                put_html(f"<div style='color:#c00; font-weight:bold; font-size:18px'>倒计时：{i}s</div>")
                        except Exception:
                            # 忽略更新失败
                            pass

                        await asyncio.sleep(1)

                    try:
                        # 超时时，若玩家已做出临时选择则确认之；否则视为放弃并跳过
                        # 特殊处理：上警阶段
                        if user.room.stage == GameStage.SHERIFF:
                            sheriff_state_inner = getattr(user.room, 'sheriff_state', {})
                            phase = sheriff_state_inner.get('phase')
                            if phase == 'signup' and not user.skill.get('sheriff_voted', False):
                                user.room.record_sheriff_choice(user, '不上警')
                            elif phase in ('vote', 'pk_vote') and user.skill.get('sheriff_vote_pending', False):
                                user.room.record_sheriff_ballot(user, '弃票')
                            elif phase == 'deferred_withdraw':
                                user.room.complete_deferred_withdraw()
                            elif (
                                user.room.day_state.get('phase') == 'await_sheriff_order' and
                                user.nick == user.room.skill.get('sheriff_captain')
                            ):
                                user.room.force_sheriff_order_random()
                        elif user.room.stage == GameStage.LAST_WORDS:
                            day_state_inner = getattr(user.room, 'day_state', {})
                            current_last = day_state_inner.get('current_last_word')
                            allow_speech = day_state_inner.get('last_words_allow_speech', True)
                            if current_last == user.nick:
                                if not user.skill.get('last_words_skill_resolved', False):
                                    user.room.handle_last_word_skill_choice(user, '放弃')
                                elif allow_speech and not user.skill.get('last_words_done', False):
                                    user.room.complete_last_word_speech(user)
                        elif user.room.stage == GameStage.BADGE_TRANSFER:
                            if user.nick == user.room.skill.get('sheriff_captain') and not user.skill.get('badge_action_taken', False):
                                user.room.handle_sheriff_badge_action(user, 'destroy')
                        elif user.room.stage in (GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE):
                            if user.skill.get('exile_vote_pending', False):
                                user.room.record_exile_vote(user, '弃票')
                        elif user.room.stage == GameStage.SPEECH:
                            if getattr(user.room, 'current_speaker', None) == user.nick:
                                user.room.advance_sheriff_speech(user.nick)
                        elif user.room.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
                            if getattr(user.room, 'current_speaker', None) == user.nick:
                                user.room.advance_exile_speech()
                        else:
                            pending_keys = ['wolf_choice', 'pending_protect', 'pending_dream_target', 'pending_target']
                            has_pending = any(user.skill.get(k) for k in pending_keys)

                            if user.role_instance and user.role_instance.needs_global_confirm and hasattr(user.role_instance, 'confirm'):
                                if has_pending:
                                    try:
                                        user.role_instance.confirm()
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        user.skip()
                                    except Exception:
                                        pass
                            else:
                                try:
                                    user.skip()
                                except Exception:
                                    pass

                        # 无论如何都发送客户端取消事件以收起输入控件
                        try:
                            get_current_session().send_client_event({'event': 'from_cancel', 'task_id': user.main_task_id, 'data': None})
                        except Exception:
                            pass
                    except Exception:
                        pass
                finally:
                    user.skill.pop('countdown_task', None)

                    # 清理倒计时显示（操作窗口内）
                    try:
                        with use_scope(make_scope_name('input_countdown', user.nick), clear=True):
                            put_html('')
                    except Exception:
                        pass
            # 仅当处于夜间阶段或上警阶段且当前玩家为能在夜间行动的角色时才启动倒计时
            try:
                is_countdown_stage = room.stage in COUNTDOWN_STAGES
            except Exception:
                is_countdown_stage = False

            if current_user.skill.get('countdown_task') is None and is_countdown_stage:
                try:
                    should_start = False
                    countdown_seconds = None
                    if room.stage == GameStage.SHERIFF:
                        phase = sheriff_state.get('phase')
                        if phase == 'signup' and not current_user.skill.get('sheriff_voted', False):
                            should_start = True
                            countdown_seconds = 10
                        elif phase in ('vote', 'pk_vote') and current_user.skill.get('sheriff_vote_pending', False):
                            should_start = True
                            countdown_seconds = 10
                        elif phase == 'deferred_withdraw':
                            active_candidates = room.get_active_sheriff_candidates() if hasattr(room, 'get_active_sheriff_candidates') else []
                            if current_user.nick in active_candidates and not current_user.skill.get('sheriff_withdrawn', False):
                                should_start = True
                                countdown_seconds = 10
                        elif day_state.get('phase') == 'await_sheriff_order' and room.skill.get('sheriff_captain') == current_user.nick:
                            should_start = True
                            countdown_seconds = 10
                    elif room.stage == GameStage.LAST_WORDS:
                        if day_state.get('current_last_word') == current_user.nick:
                            allow_speech = day_state.get('last_words_allow_speech', True)
                            if not current_user.skill.get('last_words_skill_resolved', False):
                                should_start = True
                                countdown_seconds = 10
                            elif allow_speech and not current_user.skill.get('last_words_done', False):
                                should_start = True
                                countdown_seconds = 120
                    elif room.stage == GameStage.BADGE_TRANSFER:
                        if (
                            room.skill.get('sheriff_captain') == current_user.nick and
                            not current_user.skill.get('badge_action_taken', False)
                        ):
                            should_start = True
                            countdown_seconds = 10
                    elif room.stage in (GameStage.EXILE_VOTE, GameStage.EXILE_PK_VOTE):
                        if current_user.skill.get('exile_vote_pending', False):
                            should_start = True
                            countdown_seconds = 10
                    elif room.stage == GameStage.SPEECH:
                        if getattr(room, 'current_speaker', None) == current_user.nick:
                            should_start = True
                            countdown_seconds = 120
                    elif room.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
                        if getattr(room, 'current_speaker', None) == current_user.nick:
                            should_start = True
                            if room.stage == GameStage.EXILE_SPEECH and room.skill.get('sheriff_captain') == current_user.nick:
                                countdown_seconds = 150
                            else:
                                countdown_seconds = 120
                    elif current_user.role_instance and current_user.role_instance.can_act_at_night:
                        should_start = True
                        countdown_seconds = 20

                    if should_start:
                        seconds = countdown_seconds
                        if seconds is None:
                            seconds = 10 if room.stage in DAY_TIMER_STAGES else 20
                        # 清理房间日志中遗留的倒计时私聊信息，避免旧条目继续显示在 Private 区
                        try:
                            if current_user.room and isinstance(current_user.room.log, list):
                                filtered = [e for e in current_user.room.log if not (e[0] == current_user.nick and isinstance(e[1], str) and '倒计时' in e[1])]
                                current_user.room.log = filtered
                        except Exception:
                            pass

                        task = asyncio.create_task(_countdown(current_user, seconds))
                        current_user.skill['countdown_task'] = task
                except Exception:
                    pass

            current_user.input_blocking = True
            with use_scope('input_group', clear=True):  # 替换 clear('input_group')
                # 在操作窗口内创建单行倒计时显示 scope（仅在夜间阶段或上警阶段且玩家可行动时）
                try:
                    if is_countdown_stage:
                        # 在 input_group scope 内创建一个可更新的子 scope 占位符，保证其显示在操作窗口内
                        try:
                            with use_scope(make_scope_name('input_countdown', current_user.nick), clear=True):
                                pass
                        except Exception:
                            pass
                except Exception:
                    pass

                data = await input_group('操作', inputs=ops, cancelable=True)
            current_user.input_blocking = False

            # 如果用户按下确认键，取消倒计时并调用角色确认方法（若存在）
            if data and data.get('confirm_action'):
                task = current_user.skill.pop('countdown_task', None)
                if task:
                    task.cancel()
                # 清理倒计时显示（操作窗口内）
                try:
                    with use_scope(make_scope_name('input_countdown', current_user.nick), clear=True):
                        put_html('')
                except Exception:
                    pass
                # 调用角色 confirm（若实现）
                if current_user.role_instance and hasattr(current_user.role_instance, 'confirm'):
                    try:
                        rv = current_user.role_instance.confirm()
                    except Exception as e:
                        current_user.send_msg(f'确认失败: {e}')
                # 跳过后续动作处理（confirm 已处理）
                await asyncio.sleep(0.1)
                continue


        if data is None:
            # 清理倒计时显示并跳过
            try:
                with use_scope(make_scope_name('input_countdown', current_user.nick), clear=True):
                    put_html('')
            except Exception:
                pass
            current_user.skip()
            continue

        # === Host logic ===
        if data.get('host_op') == '开始游戏':
            await room.start_game()
        if data.get('host_op') == '房间配置':
            # 房主重新配置房间
            room_config = await input_group('房间设置', inputs=[
                input(name='wolf_num', label='普通狼数', type=NUMBER, value=str(room.roles.count(Role.WOLF))),
                checkbox(name='god_wolf', label='特殊狼', inline=True, options=Role.as_god_wolf_options(),
                        value=[opt for opt in Role.as_god_wolf_options() if Role.from_option(opt) in room.roles]),
                input(name='citizen_num', label='普通村民数', type=NUMBER, value=str(room.roles.count(Role.CITIZEN))),
                checkbox(name='god_citizen', label='特殊村民', inline=True, options=Role.as_god_citizen_options(),
                        value=[opt for opt in Role.as_god_citizen_options() if Role.from_option(opt) in room.roles]),
                select(name='witch_rule', label='女巫解药规则', options=WitchRule.as_options(),
                      value=list(WitchRule.mapping().keys())[list(WitchRule.mapping().values()).index(room.witch_rule)]),
                select(name='guard_rule', label='守卫规则', options=GuardRule.as_options(),
                    value=list(GuardRule.mapping().keys())[list(GuardRule.mapping().values()).index(room.guard_rule)]),
                select(name='sheriff_bomb_rule', label='自曝警徽规则', options=SheriffBombRule.as_options(),
                    value=list(SheriffBombRule.mapping().keys())[list(SheriffBombRule.mapping().values()).index(room.sheriff_bomb_rule)]),
            ], cancelable=True)
            if room_config is None:
                current_user.send_msg('⚠️ 房间配置已取消。')
                continue
            # 更新房间配置
            from copy import copy
            roles = []
            roles.extend([Role.WOLF] * room_config['wolf_num'])
            roles.extend([Role.CITIZEN] * room_config['citizen_num'])
            roles.extend(Role.from_option(room_config['god_wolf']))
            roles.extend(Role.from_option(room_config['god_citizen']))
            room.roles = copy(roles)
            room.roles_pool = copy(roles)
            room.witch_rule = WitchRule.from_option(room_config['witch_rule'])
            room.guard_rule = GuardRule.from_option(room_config['guard_rule'])
            room.sheriff_bomb_rule = SheriffBombRule.from_option(room_config['sheriff_bomb_rule'])
            room.broadcast_msg(f'房间配置已更新：{room.desc()}')
        if data.get('sheriff_host_action') and current_user is room.get_host():
            action = data.get('sheriff_host_action')
            if action == '警长投票':
                msg = room.start_sheriff_vote(pk_mode=False)
                if msg:
                    current_user.send_msg(msg)
            elif action == '警长PK投票':
                msg = room.start_sheriff_vote(pk_mode=True)
                if msg:
                    current_user.send_msg(msg)

        if data.get('day_host_action') and current_user is room.get_host():
            action = data.get('day_host_action')
            if action == '公布昨夜信息':
                msg = await room.publish_night_info()
                if msg:
                    current_user.send_msg(msg)
            elif action == '放逐投票':
                msg = room.start_exile_vote(pk_mode=False)
                if msg:
                    current_user.send_msg(msg)
            elif action == '放逐PK投票':
                msg = room.start_exile_vote(pk_mode=True)
                if msg:
                    current_user.send_msg(msg)

        # === 夜晚行动处理（调用 role_instance） ===
        if current_user.role_instance:
            current_user.role_instance.handle_inputs(data)

        # === 上警与发言 ===
        if data.get('sheriff_vote'):
            room.record_sheriff_choice(current_user, data.get('sheriff_vote'))
            # 取消倒计时
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()
            # 不需要skip，直接继续循环刷新界面

        if data.get('sheriff_withdraw'):
            msg = room.handle_sheriff_withdraw(current_user)
            if msg:
                current_user.send_msg(msg)

        if data.get('sheriff_ballot'):
            selection = data.get('sheriff_ballot')
            target = '弃票' if selection == '弃票' else selection.split('.', 1)[-1].strip()
            room.record_sheriff_ballot(current_user, target)
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()

        if data.get('sheriff_set_order'):
            msg = room.set_sheriff_order(current_user, data.get('sheriff_set_order'))
            if msg:
                current_user.send_msg(msg)

        if data.get('last_word_skill'):
            room.handle_last_word_skill_choice(current_user, data.get('last_word_skill'))
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()

        if data.get('last_word_done'):
            room.complete_last_word_speech(current_user)
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()

        if data.get('idiot_badge_transfer'):
            msg = room.handle_idiot_badge_transfer(current_user, data.get('idiot_badge_transfer'))
            if msg:
                current_user.send_msg(msg)

        if data.get('sheriff_badge_action'):
            msg = room.handle_sheriff_badge_action(current_user, data.get('sheriff_badge_action'))
            if msg:
                current_user.send_msg(msg)
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()

        if data.get('exile_vote'):
            selection = data.get('exile_vote')
            target = '弃票' if selection == '弃票' else selection.split('.', 1)[-1].strip()
            room.record_exile_vote(current_user, target)
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()

        if data.get('wolf_self_bomb'):
            msg = room.handle_wolf_self_bomb(current_user)
            if msg:
                current_user.send_msg(msg)
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()
            await asyncio.sleep(0.3)
            continue

        if data.get('speech_done') and current_user.nick == room.current_speaker:
            task = current_user.skill.pop('countdown_task', None)
            if task:
                task.cancel()
            current_user.skip()
            if room.stage == GameStage.SPEECH:
                room.advance_sheriff_speech(current_user.nick)
            elif room.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
                room.advance_exile_speech()

        # 防止按钮闪烁
        await asyncio.sleep(0.3)


# ==================== 启动入口（Mac 优化 + pyngrok） ====================
if __name__ == '__main__':
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def stop_server(signum, frame):
        logger.info("正在关闭服务器...")
        loop = get_pywebio_ioloop()
        if loop is None:
            return
        try:
            loop.add_callback_from_signal(loop.stop)
        except AttributeError:
            loop.add_callback(loop.stop)
    signal.signal(signal.SIGINT, stop_server)

    # 默认端口，可通过环境变量 `PORT` 覆盖（方便在端口被占用时切换）
    port = int(os.environ.get('PORT', '8080'))
    ip = get_interface_ip()

    ngrok_url = None
    if os.environ.get('DISABLE_NGROK', '').lower() in ('1', 'true', 'yes'):
        print("已检测到 DISABLE_NGROK，跳过 ngrok 连接，服务仅在局域网可见。")
    else:
        try:
            # 如果没有提供 authtoken，则跳过 ngrok（避免频繁出现认证错误日志）
            if not os.environ.get('NGROK_AUTHTOKEN') and not os.environ.get('NGROK_AUTH_TOKEN'):
                raise RuntimeError('未提供 NGROK_AUTHTOKEN，跳过 ngrok 连接')

            public_url = ngrok.connect(port, bind_tls=True)
            ngrok_url = str(public_url).replace("NgrokTunnel: \"", "").replace("\"", "")
            print("\n" + "="*70)
            print("       狼人杀已上线！全球可玩！")
            print(f"       局域网地址 → http://{ip}:{port}")
            print(f"       公网地址 → {ngrok_url}")
            print("       分享这个链接给所有玩家：")
            print(f"       {ngrok_url}")
            print("="*70 + "\n")
        except Exception as e:
            print(f"ngrok 启动失败（可能是网络或未授权）：{e}")
            print(f"仅限局域网：http://{ip}:{port}")
            ngrok_url = None

    logger.info(f"狼人杀服务器启动成功！")
    logger.info(f"局域网访问：http://{ip}:{port}")
    if ngrok_url:
        logger.info(f"外网访问：{ngrok_url}")

    start_server(
        main,
        debug=False,
        host='0.0.0.0',
        port=port,
        cdn=False,
        auto_open_webbrowser=False,
        websocket_ping_interval=25,
        allowed_origins=["*"],
    )
