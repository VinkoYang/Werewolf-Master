# models/lobby.py  – pure data module (no UI framework dependencies)
from collections import Counter
from copy import deepcopy
from typing import Optional

from enums import GuardRule, Role, SheriffBombRule, WitchRule
from presets.game_config_presets import DEFAULT_ROOM_RULES, PRESET_CUSTOM
from presets.game_config_registry import (
    get_special_preset_sections,
    get_special_preset_templates,
)
from models.room import Room
from models.system import Global

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
    ('狼人杀发言常用术语', 'https://zh.wikiversity.org/zh-hans/%E7%8B%BC%E4%BA%BA%E6%AE%BA/%E7%99%BC%E8%A8%80%E5%B8%B8%E7%94%A8%E8%A1%93%E8%AA%9E'),
    ('狼人杀手势大全', 'https://zhuanlan.zhihu.com/p/408899488'),
    ('全网最全狼人杀手势[视频]', 'https://www.bilibili.com/video/BV17PdSYSEmA/'),
    ('玩狼人杀经常站错边怎么办？', 'https://www.langrensha.net/strategy/2024030101.html'),
    ('三个关键步骤提升你的狼人杀水平', 'https://www.langrensha.net/strategy/'),
    ('狼人杀贴脸发言是什么意思', 'https://www.langrensha.net/strategy/2021111601.html'),
]

DEV_LINKS = [
    ('GitHub：VinkoYang', 'https://github.com/VinkoYang'),
    ('小红书：杨小格', 'https://www.xiaohongshu.com/user/profile/5756313f3460947ce75fb8f0'),
    ('关于本站 / GitHub 仓库', 'https://github.com/VinkoYang/Werewolf-Master'),
]

FEEDBACK_LINK = ('提交修改意见（GitHub Issues）', 'https://github.com/VinkoYang/Werewolf-Master/issues/new')


def _format_role_config_summary(roles) -> str:
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


def resolve_room_config(preset_choice: str, custom_data: Optional[dict] = None) -> Optional[dict]:
    """Return a room config dict for the given preset key, or None if invalid."""
    if preset_choice == PRESET_CUSTOM:
        return custom_data
    template = ROOM_PRESET_CONFIGS.get(preset_choice)
    if not template:
        return None
    return deepcopy(template)


def build_roles_from_config(config: dict) -> list:
    """Convert a room config dict into a flat Role list."""
    roles = []
    roles.extend([Role.WOLF] * int(config.get('wolf_num', 0)))
    roles.extend([Role.CITIZEN] * int(config.get('citizen_num', 0)))
    roles.extend(Role.from_option(config.get('god_wolf', [])))
    roles.extend(Role.from_option(config.get('god_citizen', [])))
    return roles
