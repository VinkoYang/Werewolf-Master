"""12-player preset：预女猎白混。"""

from enums import Role

from .game_config_base import DefaultGameFlow
from .game_config_presets import PRESET_HALF_BLOOD_MIX, SPECIAL_PRESET_SECTION

ROLE_LIST = [
    Role.WOLF,
    Role.WOLF,
    Role.WOLF,
    Role.WOLF,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.HALF_BLOOD,
    Role.SEER,
    Role.WITCH,
    Role.HUNTER,
    Role.IDIOT,
]


class HalfBloodMixGameConfig(DefaultGameFlow):
    """Half-blood mix preset reusing the default flow."""

    pass


PRESET_METADATA = {
    'key': PRESET_HALF_BLOOD_MIX,
    'label': '预女猎白混',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'info',
    'roles': ROLE_LIST,
    'config_cls': HalfBloodMixGameConfig,
}
