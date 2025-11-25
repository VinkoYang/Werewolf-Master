"""黑狼王 - 预女猎守 版型配置。"""

from enums import GameStage, Role

from .game_config_base import DefaultGameFlow
from .game_config_presets import PRESET_WOLF_KING_GUARD, SPECIAL_PRESET_SECTION

ROLE_LIST = [
    Role.WOLF_KING,
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


class WolfKingGuardGameConfig(DefaultGameFlow):
    """Black wolf king guard board (wolf king acts last)."""

    def night_role_order(self):
        return [
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.HUNTER, [Role.HUNTER]),
            (GameStage.WOLF_KING, [Role.WOLF_KING]),
        ]


PRESET_METADATA = {
    'key': PRESET_WOLF_KING_GUARD,
    'label': '黑狼王 - 预女猎守',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'danger',
    'roles': ROLE_LIST,
    'config_cls': WolfKingGuardGameConfig,
}
