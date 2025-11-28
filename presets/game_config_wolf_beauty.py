"""狼美人 - 预女猎守 版型配置。

行动顺序：狼人+狼美人 → 狼美人 → 守卫 → 预言家 → 女巫 → 猎人

狼美人技能：
- 可以魅惑一名玩家
- 狼美人出局时被魅惑者殉情死亡且无技能
"""

from enums import GameStage, Role

from .base import DefaultGameFlow
from .game_config_presets import SPECIAL_PRESET_SECTION

PRESET_WOLF_BEAUTY = 'preset_wolf_beauty'

ROLE_LIST = [
    Role.WOLF_BEAUTY,
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


class WolfBeautyGameConfig(DefaultGameFlow):
    """Wolf Beauty board config (12人狼美人版)."""

    def night_role_order(self):
        """行动顺序：狼美人 → 守卫 → 预言家 → 女巫 → 猎人"""
        return [
            (GameStage.WOLF_BEAUTY, [Role.WOLF_BEAUTY]),
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.HUNTER, [Role.HUNTER]),
        ]


PRESET_METADATA = {
    'key': PRESET_WOLF_BEAUTY,
    'label': '狼美人 - 预女猎守',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'secondary',
    'roles': ROLE_LIST,
    'config_cls': WolfBeautyGameConfig,
}
