import asyncio
import json
from collections import Counter
from copy import deepcopy
from typing import Callable, Optional

from pywebio.input import NUMBER, checkbox, input, input_group, select
from pywebio.output import (
    close_popup,
    popup,
    put_button,
    put_buttons,
    put_column,
    put_markdown,
    put_row,
    put_scope,
    put_text,
    style,
    toast,
    use_scope,
)
from pywebio.pin import pin_on_change, pin_update, put_input
from pywebio.session import run_js

from enums import GuardRule, Role, SheriffBombRule, WitchRule
from presets.game_config_presets import (
    DEFAULT_ROOM_RULES,
    PRESET_CUSTOM,
)
from presets.game_config_registry import (
    get_special_preset_sections,
    get_special_preset_templates,
)
from models.room import Room
from models.system import Global
from models.user import User
from utils import make_scope_name

PRESET_DEV_3 = 'preset_dev_3'
PRESET_DEV_6 = 'preset_dev_6'
PRESET_DEV_7 = 'preset_dev_7'

SPECIAL_PRESET_TEMPLATES = get_special_preset_templates()

ROOM_PRESET_CONFIGS = {
    **SPECIAL_PRESET_TEMPLATES,
    PRESET_DEV_3: {
        **DEFAULT_ROOM_RULES,
        'wolf_num': 1,
        'god_wolf': [],
        'citizen_num': 1,
        'god_citizen': ['预言家'],
    },
    PRESET_DEV_6: {
        **DEFAULT_ROOM_RULES,
        'wolf_num': 1,
        'god_wolf': [],
        'citizen_num': 1,
        'god_citizen': ['预言家', '女巫', '守卫', '猎人'],
    },
    PRESET_DEV_7: {
        **DEFAULT_ROOM_RULES,
        'wolf_num': 2,
        'god_wolf': [],
        'citizen_num': 1,
        'god_citizen': ['预言家', '女巫', '守卫', '猎人'],
    },
}

ROOM_CREATION_SECTIONS = [
    ('自定义', [
        {'label': '手动配置', 'value': PRESET_CUSTOM, 'color': 'primary'},
    ]),
    *get_special_preset_sections(),
    ('开发者测试版型', [
        {'label': '3人测试板子', 'value': PRESET_DEV_3},
        {'label': '预女猎守1狼6人测试', 'value': PRESET_DEV_6},
        {'label': '预女猎守2狼7人测试', 'value': PRESET_DEV_7},
    ]),
]

GAME_RESOURCE_LINKS = [
    ('狼人杀法典', 'https://lanke.fun/wp-content/uploads/2024/06/%E7%AC%AC%E4%BA%8C%E7%89%88%E7%8B%BC%E4%BA%BA%E6%9D%80%E6%B3%95%E5%85%B8.pdf'),
    ('对局版型', 'https://github.com/VinkoYang/Werewolf-Master/blob/main/configuration.md'),
    ('角色介绍', 'https://github.com/VinkoYang/Werewolf-Master/blob/main/roles.md'),
    ('游戏规则', 'https://github.com/VinkoYang/Werewolf-Master/blob/main/rules.md'),
]

GUIDE_LINKS = [
    ('新手玩家狼人杀指南', 'https://www.langrensha.net/strategy/2024021801.html'),
    ('狼人杀发言常用术语','https://zh.wikiversity.org/zh-hans/%E7%8B%BC%E4%BA%BA%E6%AE%BA/%E7%99%BC%E8%A8%80%E5%B8%B8%E7%94%A8%E8%A1%93%E8%AA%9E'),
    ('狼人杀手势大全','https://zhuanlan.zhihu.com/p/408899488'),
    ('全网最全狼人杀手势[视频]', 'https://www.bilibili.com/video/BV17PdSYSEmA/?spm_id_from=333.788.recommend_more_video.5&vd_source=6d7b9125c1b0246ab38a0f2e83833e06'),
    ('玩狼人杀经常站错边怎么办？', 'https://www.langrensha.net/strategy/2024030101.html'),
    ('三个关键步骤提升你的狼人杀水平', 'https://www.langrensha.net/strategy/'),
    ('狼人杀贴脸发言是什么意思', 'https://www.langrensha.net/strategy/2021111601.html'),
]

DEV_LINKS = [
    ('GitHub：VinkoYang', 'https://github.com/VinkoYang'),
    ('小红书：杨小格', 'https://www.xiaohongshu.com/user/profile/5756313f3460947ce75fb8f0?xsec_token=YBAxQoJOG155s5e7YxSclMUzii5s86HzFyoVOzG9g7oxo=&xsec_source=app_share&xhsshare=CopyLink&shareRedId=Nz03NjY2N085OzsyQjw3SUs6Pk1GPUw5&apptime=1764022958&share_id=ad63e284c04d420a82a08455899a351d'),
    ('关于本站 / GitHub 仓库', 'https://github.com/VinkoYang/Werewolf-Master'),
]

FEEDBACK_LINK = ('提交修改意见（GitHub Issues）', 'https://github.com/VinkoYang/Werewolf-Master/issues/new')


def _make_link_button(label: str, url: str, color: str = 'secondary'):
    safe_url = json.dumps(url)
    return put_button(
        label,
        onclick=lambda s=safe_url: run_js(f"window.open({s}, '_blank')"),
        color=color,
        outline=True
    )


def _format_role_config_summary(roles):
    counter = Counter(roles)
    if not counter:
        return '暂未配置角色'
    parts = []
    for role, count in sorted(counter.items(), key=lambda item: item[0].value):
        label = role.value
        parts.append(f"{label}x{count}" if count > 1 else label)
    return '、'.join(parts)


def build_room_info_lines() -> list:
    rooms = list(Global.rooms.values())
    if not rooms:
        return []
    lines = []
    for room in sorted(rooms, key=lambda r: r.id or 0):
        current = len(room.players)
        total = len(room.roles)
        config = _format_role_config_summary(room.roles)
        lines.append({
            'room_id': str(room.id),
            'text': f"{room.id}号：{current}/{total} 人｜{config}"
        })
    return lines


def update_room_info_panel(scope_name: str, on_select: Optional[Callable[[str], None]] = None):
    lines = build_room_info_lines()
    entries = []
    if not lines:
        entries.append(put_text('暂无可加入的房间，请先创建一个房间。'))
    else:
        for entry in lines:
            if on_select:
                entries.append(
                    put_button(
                        entry['text'],
                        onclick=lambda rid=entry['room_id']: on_select(rid),
                        color='light'
                    )
                )
            else:
                entries.append(put_text(entry['text']))
    entries.append(put_button('刷新', onclick=lambda: update_room_info_panel(scope_name, on_select), color='info'))
    with use_scope(scope_name, clear=True):
        put_column(entries)


async def prompt_room_join(current_user: User) -> Optional[str]:
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    pin_name = make_scope_name('room_join_id', current_user.nick)
    scope_name = make_scope_name('room_info_panel', current_user.nick)
    room_selection = {'room_id': ''}

    def _on_room_input(value):
        room_selection['room_id'] = (value or '').strip()

    def _submit_join():
        value = room_selection['room_id']
        error = Room.validate_room_join(value)
        if error:
            toast(error, color='error')
            return
        if future.done():
            return
        close_popup()
        future.set_result(value)

    def _cancel():
        if future.done():
            return
        close_popup()
        future.set_result(None)

    def _quick_join(room_id: str):
        room_selection['room_id'] = room_id
        try:
            pin_update(pin_name, value=room_id)
        except Exception:
            pass
        _submit_join()

    header = put_row([
        put_text('加入房间'),
        put_button('✕', onclick=_cancel, color='danger', outline=True)
    ], size='90% 10%')

    content = put_column([
        header,
        put_row([
            put_input(pin_name, type='text', placeholder='输入房间号', value=''),
            put_button('加入房间', onclick=_submit_join, color='success')
        ], size='70% 30%'),
        put_markdown('#### 房间信息'),
        put_scope(scope_name)
    ])

    popup('加入房间', content, closable=False)
    pin_on_change(pin_name, _on_room_input)
    _on_room_input('')
    update_room_info_panel(scope_name, on_select=_quick_join)
    result = await future
    return result


async def prompt_seat_selection(room: Room, current_user: User) -> Optional[int]:
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    scope_name = make_scope_name('seat_selector', current_user.nick)

    def _cancel():
        if future.done():
            return
        close_popup()
        future.set_result(None)

    def _choose(seat: int):
        if future.done():
            return
        available = set(room.list_available_seats())
        if seat not in available:
            toast('该座位已被占用或超出范围，请重新选择', color='error')
            _render()
            return
        close_popup()
        future.set_result(seat)

    def _render():
        total = len(room.roles)
        taken = {u.seat: u.nick for u in room.players.values() if u.seat}
        available = set(room.list_available_seats())
        body = []
        if not available:
            body.append(put_text('房间暂时无可用座位。'))
            body.append(put_button('返回大厅', onclick=_cancel, color='danger'))
        else:
            buttons = []
            for seat in range(1, total + 1):
                occupant = taken.get(seat)
                label = f"{seat}号" + (f"（{occupant}）" if occupant else '')
                buttons.append({
                    'label': label,
                    'value': seat,
                    'color': 'success' if seat in available else 'secondary',
                    'disabled': seat not in available,
                })
            body.append(put_markdown('#### 请选择你的座位号'))
            body.append(put_buttons(buttons, onclick=_choose))
            body.append(put_text('绿色按钮表示可选座位，灰色为已被占用。'))
            body.append(put_button('刷新座位情况', onclick=_render, color='info', outline=True))
        with use_scope(scope_name, clear=True):
            put_column(body)

    header = put_row([
        put_text(f'选择座位（房间 {room.id or "未编号"}）'),
        put_button('✕', onclick=_cancel, color='danger', outline=True)
    ], size='85% 15%')

    popup('选择座位', put_column([header, put_scope(scope_name)]), closable=False)
    _render()
    result = await future
    return result


async def select_room_creation_preset() -> Optional[str]:
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    def _resolve(value):
        if future.done():
            return
        future.set_result(value)
        close_popup()

    def _cancel():
        if future.done():
            return
        future.set_result(None)
        close_popup()

    section_blocks = []
    for title, buttons in ROOM_CREATION_SECTIONS:
        section_blocks.append(put_markdown(f"### {title}"))
        section_blocks.append(put_buttons(buttons, onclick=_resolve))

    close_btn = style(
        put_button('✕', onclick=_cancel, color='danger', outline=True),
        "position: absolute; top: 0; right: 0; z-index: 10;"
    )

    dialog_body = style(
        put_column([close_btn, put_column(section_blocks)]),
        "position: relative; max-height: calc(100vh - 260px); overflow-y: auto;"
    )

    popup(
        '创建房间',
        style(dialog_body, "display:inline-block; min-height:0;"),
        'width:auto; max-width:520px; height:auto; min-height:0; padding:0;',
        closable=False
    )
    choice = await future
    return choice


async def prompt_room_creation() -> Optional[dict]:
    preset_choice = await select_room_creation_preset()
    if not preset_choice:
        return None
    if preset_choice == PRESET_CUSTOM:
        room_config = await input_group('房间设置', inputs=[
            input(name='wolf_num', label='普通狼数', type=NUMBER, value='3'),
            checkbox(name='god_wolf', label='特殊狼', inline=True, options=Role.as_god_wolf_options()),
            input(name='citizen_num', label='普通村民数', type=NUMBER, value='4'),
            checkbox(name='god_citizen', label='特殊村民', inline=True,
                     options=Role.as_god_citizen_options()),
            select(name='witch_rule', label='女巫解药规则', options=WitchRule.as_options()),
            select(name='guard_rule', label='守卫规则', options=GuardRule.as_options()),
            select(name='sheriff_bomb_rule', label='自曝警徽规则', options=SheriffBombRule.as_options(), value=SheriffBombRule.DOUBLE_LOSS.value),
        ], cancelable=True)
        if not room_config:
            return None
        return room_config
    template = ROOM_PRESET_CONFIGS.get(preset_choice)
    if not template:
        raise ValueError(f'未知的房间预设：{preset_choice}')
    return deepcopy(template)


async def wait_lobby_selection(scope_name: str) -> str:
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    def _on_click(value):
        if future.done():
            return
        future.set_result(value)

    card_style = 'background:#FAF3E1;border:1px solid #E4D5B3;border-radius:12px;padding:14px 18px;margin-bottom:16px;color:#2b2b2b;display:block;width:100%;max-width:100%;box-shadow:0 2px 6px rgba(0,0,0,0.04);'

    def _make_section(title: str, body_widgets):
        return style(
            put_column([put_markdown(f"### {title}")] + body_widgets),
            card_style
        )

    dev_widgets = [put_text('杨小格 @ Vinko_Yang')]
    for label, url in DEV_LINKS:
        dev_widgets.append(_make_link_button(label, url, 'info'))

    feedback_section = _make_section('反馈区', [
        _make_link_button(FEEDBACK_LINK[0], FEEDBACK_LINK[1], 'danger')
    ])

    lobby_header = style(
        put_row([
            put_markdown('## 🎮 大厅'),
            put_button('创建房间', onclick=lambda: _on_click('创建房间'), color='success'),
            put_button('加入房间', onclick=lambda: _on_click('加入房间'), color='primary')
        ], size='auto'),
        'display:flex;align-items:center;gap:15px;padding:12px 16px;border:1px solid #E4D5B3;border-radius:12px;background:#FFF8EA;flex-wrap:wrap;box-shadow:0 1px 4px rgba(0,0,0,0.04);width:100%;'
    )

    page_sections = [
        lobby_header,
        _make_section('游戏资料区', [_make_link_button(label, url, 'dark') for label, url in GAME_RESOURCE_LINKS]),
        _make_section('攻略 & 新手指南', [_make_link_button(label, url, 'warning') for label, url in GUIDE_LINKS]),
        _make_section('开发者信息', dev_widgets),
        _make_section('关于本站 & 开源', [_make_link_button('GitHub 开源链接', 'https://github.com/VinkoYang/Werewolf-Master', 'secondary')]),
        feedback_section
    ]

    with use_scope(scope_name, clear=True):
        put_column([
            style(
                put_column(page_sections),
                'width:100%;margin:0 auto;display:flex;flex-direction:column;gap:18px;'
            )
        ])

    return await future
