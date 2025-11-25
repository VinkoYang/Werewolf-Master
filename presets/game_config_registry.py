"""Central registry for special game configurations and lobby presets."""

from __future__ import annotations

from collections import Counter, OrderedDict
from typing import Dict, List, Sequence, Tuple, Type

from enums import Role

from .game_config_base import BaseGameConfig
from .game_config_general import GeneralGameConfig
from .game_config_presets import DEFAULT_ROOM_RULES
from .game_config_12p_std import PRESET_METADATA as STD_METADATA
from .game_config_12p_half_blood_mix import PRESET_METADATA as MIX_METADATA
from .game_config_white_wolf_guard import PRESET_METADATA as WHITE_METADATA
from .game_config_wolf_king_guard import PRESET_METADATA as WKG_METADATA
from .game_config_wolf_king_dreamer import PRESET_METADATA as WKD_METADATA

MetadataDict = Dict[str, object]


def _append_role_entries(bucket: List[str], counter: Counter, role: Role):
    for _ in range(counter.get(role, 0)):
        bucket.append(role.value)


def _build_room_template(counter: Counter) -> Dict[str, object]:
    template = dict(DEFAULT_ROOM_RULES)
    template.update({
        'wolf_num': counter.get(Role.WOLF, 0),
        'citizen_num': counter.get(Role.CITIZEN, 0),
        'god_wolf': [],
        'god_citizen': [],
    })

    god_wolf_bucket: List[str] = template['god_wolf']  # type: ignore[assignment]
    for special in (Role.WOLF_KING, Role.WHITE_WOLF_KING):
        _append_role_entries(god_wolf_bucket, counter, special)

    god_citizen_bucket: List[str] = template['god_citizen']  # type: ignore[assignment]
    for special in (Role.SEER, Role.WITCH, Role.GUARD, Role.HUNTER, Role.DREAMER, Role.IDIOT, Role.HALF_BLOOD):
        _append_role_entries(god_citizen_bucket, counter, special)

    return template


def _hydrate_descriptor(metadata: MetadataDict) -> MetadataDict:
    roles: Sequence[Role] = metadata['roles']  # type: ignore[assignment]
    role_counter = Counter(roles)
    descriptor = dict(metadata)
    descriptor['role_counter'] = role_counter
    descriptor['room_template'] = _build_room_template(role_counter)
    return descriptor


_SPECIAL_CONFIGS: List[MetadataDict] = [
    _hydrate_descriptor(STD_METADATA),
    _hydrate_descriptor(MIX_METADATA),
    _hydrate_descriptor(WHITE_METADATA),
    _hydrate_descriptor(WKG_METADATA),
    _hydrate_descriptor(WKD_METADATA),
]


def resolve_game_config_class(roles: Sequence[Role]) -> Type[BaseGameConfig]:
    role_counter = Counter(roles)
    for desc in _SPECIAL_CONFIGS:
        if role_counter == desc['role_counter']:
            return desc['config_cls']  # type: ignore[return-value]
    return GeneralGameConfig


def get_special_preset_templates() -> Dict[str, Dict[str, object]]:
    return {desc['key']: desc['room_template'] for desc in _SPECIAL_CONFIGS}


def get_special_preset_sections() -> List[Tuple[str, List[Dict[str, object]]]]:
    grouped: "OrderedDict[str, List[MetadataDict]]" = OrderedDict()
    for desc in _SPECIAL_CONFIGS:
        section = desc.get('section', '特殊版型')
        grouped.setdefault(section, []).append(desc)

    sections: List[Tuple[str, List[Dict[str, object]]]] = []
    for section, entries in grouped.items():
        buttons = [
            {
                'label': entry['label'],
                'value': entry['key'],
                'color': entry.get('button_color', 'secondary'),
            }
            for entry in entries
        ]
        sections.append((section, buttons))
    return sections


def describe_registered_presets() -> List[MetadataDict]:
    """Expose raw descriptors (useful for diagnostics/tests)."""
    return list(_SPECIAL_CONFIGS)