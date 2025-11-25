"""白狼王 - 预女猎守 版型配置。"""

from enums import GameStage, Role

from .base import DefaultGameFlow
from .game_config_presets import PRESET_WHITE_WOLF_GUARD, SPECIAL_PRESET_SECTION

ROLE_LIST = [
    Role.WHITE_WOLF_KING,
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


class WhiteWolfGuardGameConfig(DefaultGameFlow):
    """White wolf king board with guard in the first god action slot."""

    def night_role_order(self):
        return [
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.HUNTER, [Role.HUNTER]),
        ]


PRESET_METADATA = {
    'key': PRESET_WHITE_WOLF_GUARD,
    'label': '白狼王 - 预女猎守',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'warning',
    'roles': ROLE_LIST,
    'config_cls': WhiteWolfGuardGameConfig,
}
