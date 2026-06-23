"""机械狼-镜隐迷踪 版型配置。

角色构成（12人）：
  1 觉醒隐狼 + 3 普狼 + 4 平民 + 1 魔镜少女 + 1 女巫 + 1 猎人 + 1 守卫

夜晚行动顺序（每晚第一阶段为机械狼阶段）：
  机械狼阶段 → 普狼 → 守卫 → 魔镜少女 → 女巫 → 猎人

机械狼阶段规则：
  - 未学技能：学习（宣告"机械狼请出现/请闭眼"）
  - 已学技能（学于上一晚或更早）：行动（宣告"机械狼行动请出现/请闭眼"）
"""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from enums import GameStage, PlayerStatus, Role
from presets.base import DefaultGameFlow, WOLF_CAMP_ROLES, GOD_ROLES, VILLAGER_ROLES, THIRD_PARTY_ROLES
from presets.game_config_presets import PRESET_MECHANICAL_WOLF_MIRROR, SPECIAL_PRESET_SECTION

if TYPE_CHECKING:
    pass

ROLE_LIST = [
    Role.MECHANICAL_WOLF,
    Role.WOLF,
    Role.WOLF,
    Role.WOLF,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.CITIZEN,
    Role.MAGIC_MIRROR_GIRL,
    Role.WITCH,
    Role.HUNTER,
    Role.GUARD,
]


class MechanicalWolfMirrorGameConfig(DefaultGameFlow):
    """机械狼-镜隐迷踪 board."""

    async def handle_custom_pre_wolf_stages(self):
        """每晚第一阶段：未学技能则学习，已学技能（上一晚起）则行动。"""
        room = self.room
        mw = next(
            (u for u in room.players.values()
             if u.role == Role.MECHANICAL_WOLF and u.status != PlayerStatus.DEAD),
            None
        )
        if (mw
                and mw.skill.get('learned_role')
                and mw.skill.get('learned_night', room.round) < room.round):
            await self.run_role_stage(GameStage.MECHANICAL_WOLF_ACT, [Role.MECHANICAL_WOLF])
        else:
            await self.run_role_stage(GameStage.MECHANICAL_WOLF_LEARN, [Role.MECHANICAL_WOLF])

    def night_role_order(self) -> List[tuple]:
        return [
            (GameStage.GUARD,             [Role.GUARD]),
            (GameStage.MAGIC_MIRROR_GIRL, [Role.MAGIC_MIRROR_GIRL]),
            (GameStage.WITCH,             [Role.WITCH]),
            (GameStage.HUNTER,            [Role.HUNTER]),
        ]

    async def check_game_end(self):
        """Override to count MECHANICAL_WOLF as part of the wolf camp."""
        room = self.room
        alive = room.list_alive_players()

        initial_god_present = any(p.role in GOD_ROLES for p in room.players.values())
        initial_villager_present = any(p.role in VILLAGER_ROLES for p in room.players.values())
        initial_third_present = any(p.role in THIRD_PARTY_ROLES for p in room.players.values())

        wolves: list = []
        goods: list = []
        third_party: list = []
        alive_gods: list = []
        alive_villagers: list = []

        for user in alive:
            role = user.role
            if role is None:
                continue
            if role == Role.HALF_BLOOD and user.skill.get('half_blood_camp', 'good') == 'wolf':
                wolves.append(user)
                continue
            if role in WOLF_CAMP_ROLES:
                wolves.append(user)
                continue
            if role in THIRD_PARTY_ROLES:
                third_party.append(user)
                continue
            goods.append(user)
            if role in GOD_ROLES:
                alive_gods.append(user)
            if role in VILLAGER_ROLES:
                alive_villagers.append(user)

        third_alive = bool(third_party)
        wolf_border_met = False
        if initial_god_present and not alive_gods:
            wolf_border_met = True
        if initial_villager_present and not alive_villagers:
            wolf_border_met = True

        if initial_third_present:
            wolf_win = wolf_border_met and not third_alive
            good_win = (not wolves) and not third_alive
            third_win = third_alive and not wolves and not goods
        else:
            wolf_win = wolf_border_met
            good_win = not wolves

        if wolf_win:
            await self.end_game("狼人阵营获胜！完成屠边")
        elif initial_third_present and third_win:
            await self.end_game("第三方阵营获胜！完成屠城")
        elif good_win:
            await self.end_game("好人阵营获胜！狼人全部出局")


PRESET_METADATA = {
    'key': PRESET_MECHANICAL_WOLF_MIRROR,
    'label': '机械狼 - 镜隐迷踪',
    'section': SPECIAL_PRESET_SECTION,
    'button_color': 'danger',
    'roles': ROLE_LIST,
    'config_cls': MechanicalWolfMirrorGameConfig,
}
