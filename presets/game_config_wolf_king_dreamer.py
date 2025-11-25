"""黑狼王 - 预女猎摄 版型配置。"""

from enums import GameStage, Role

from .base import DefaultGameFlow
from .game_config_presets import PRESET_WOLF_KING_DREAMER, SPECIAL_PRESET_SECTION

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
    Role.DREAMER,
]


class WolfKingDreamerGameConfig(DefaultGameFlow):
    """Black wolf king dreamer board (dreamer acts before wolves)."""

    def night_role_order(self):
        return [
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.HUNTER, [Role.HUNTER]),
            (GameStage.WOLF_KING, [Role.WOLF_KING]),
        ]

    async def handle_custom_pre_wolf_stages(self):
        await self.run_role_stage(GameStage.DREAMER, [Role.DREAMER])


PRESET_METADATA = {
    'key': PRESET_WOLF_KING_DREAMER,
    'label': '黑狼王 - 预女猎摄',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'secondary',
    'roles': ROLE_LIST,
    'config_cls': WolfKingDreamerGameConfig,
}
