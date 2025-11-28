from enums import GuardRule, SheriffBombRule, WitchRule

# Shared default rules applied to all preset room templates unless overridden.
DEFAULT_ROOM_RULES = {
    'witch_rule': WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY.value,
    'guard_rule': GuardRule.MED_CONFLICT.value,
    'sheriff_bomb_rule': SheriffBombRule.DOUBLE_LOSS.value,
}

# Common preset identifiers exposed to the lobby and config registry.
PRESET_CUSTOM = 'preset_custom'
PRESET_STANDARD_12 = 'preset_standard_12'
PRESET_HALF_BLOOD_MIX = 'preset_half_blood_mix'
PRESET_WHITE_WOLF_GUARD = 'preset_white_wolf_guard'
PRESET_WOLF_KING_GUARD = 'preset_wolf_king_guard'
PRESET_WOLF_KING_DREAMER = 'preset_wolf_king_dreamer'
PRESET_NINE_TAILED_FOX = 'preset_nine_tailed_fox'
PRESET_NIGHTMARE = 'preset_nightmare'
PRESET_WOLF_BEAUTY = 'preset_wolf_beauty'

SPECIAL_PRESET_SECTION = '12人版型'
