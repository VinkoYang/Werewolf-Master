"""梦魇 - 预女猎守 版型配置。

行动顺序：梦魇单独睁眼 → 梦魇闭眼 → 普狼+梦魇一起睁眼 → 守卫 → 预言家 → 女巫 → 猎人

注意：梦魇阶段已在 DefaultGameFlow._run_nightmare_stage_if_needed() 中统一处理，
任何包含梦魇的版型都会自动执行梦魇阶段。
"""

from enums import GameStage, Role

from .base import DefaultGameFlow
from .game_config_presets import PRESET_NIGHTMARE, SPECIAL_PRESET_SECTION

ROLE_LIST = [
    Role.NIGHTMARE,
    Role.WOLF,
    Role.WOLF,
    Role.WOLF,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.SEER,
    Role.WITCH,
    Role.HUNTER,
    Role.GUARD,
]


class NightmareGameConfig(DefaultGameFlow):
    """Nightmare board config (12人标准梦魇版)."""

    def night_role_order(self):
        """行动顺序：守卫 → 预言家 → 女巫 → 猎人"""
        return [
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.HUNTER, [Role.HUNTER]),
        ]


PRESET_METADATA = {
    'key': PRESET_NIGHTMARE,
    'label': '梦魇 - 预女猎守',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'secondary',
    'roles': ROLE_LIST,
    'config_cls': NightmareGameConfig,
}
