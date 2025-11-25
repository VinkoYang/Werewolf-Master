"""预女猎尾（九尾妖狐）版型配置。"""

from enums import GameStage, Role

from .game_config_base import DefaultGameFlow
from .game_config_presets import PRESET_NINE_TAILED_FOX, SPECIAL_PRESET_SECTION

ROLE_LIST = [
    Role.WOLF,
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
    Role.NINE_TAILED_FOX,
]


class NineTailedFoxGameConfig(DefaultGameFlow):
    """Preset featuring the 九尾妖狐 stage after猎人。"""

    def night_role_order(self):
        return [
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.HUNTER, [Role.HUNTER]),
            (GameStage.NINE_TAILED_FOX, [Role.NINE_TAILED_FOX]),
        ]


PRESET_METADATA = {
    'key': PRESET_NINE_TAILED_FOX,
    'label': '预女猎尾',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'success',
    'roles': ROLE_LIST,
    'config_cls': NineTailedFoxGameConfig,
}
