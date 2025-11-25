"""Specialized 12-player standard game configuration."""

from enums import Role

from .base import DefaultGameFlow
from .game_config_presets import PRESET_STANDARD_12, SPECIAL_PRESET_SECTION


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
    Role.IDIOT,
]


class Standard12pGameConfig(DefaultGameFlow):
    """Standard 12p preset flow (overrides go here if needed)."""

    pass


PRESET_METADATA = {
    'key': PRESET_STANDARD_12,
    'label': '12人标准局：预女猎白',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'success',
    'roles': ROLE_LIST,
    'config_cls': Standard12pGameConfig,
}
