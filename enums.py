# enums.py
from enum import Enum
from typing import Union

# 广播控制类（目前只有一个值：移除输入框）
class LogCtrl(Enum):
    """广播目标为 None 的，特殊控制消息类型枚举"""
    RemoveInput = '移除当前输入框'

# 对 Enum 做了一个小封装，使 __repr__/__str__ 直接返回枚举的 value（便于打印中文）。
class PlainEnum(Enum):
    def __repr__(self):
        return self.value
    __str__ = __repr__

# 玩家状态的枚举（存活/出局/被毒/被救/被守等中间态）。
class PlayerStatus(PlainEnum):
    ALIVE = '存活'
    DEAD = '出局'
    PENDING_DEAD = '被狼人/女巫/守救冲突杀害'
    PENDING_HEAL = '被女巫解救'
    PENDING_POISON = '被女巫毒害'
    PENDING_GUARD = '被守卫守护'

# 游戏阶段（白天、狼人、预言家、女巫、守卫、猎人、摄梦人、上警、竞选发言）
class GameStage(Enum):
    Day = 'Day'
    WOLF = '狼人'
    SEER = '预言家'
    WITCH = '女巫'
    GUARD = '守卫'
    HUNTER = '猎人'
    DREAMER = '摄梦人'          # 摄梦人阶段
    SHERIFF = '上警'           # 上警阶段
    SPEECH = '发言'            # 竞选发言阶段
    LAST_WORDS = '遗言'
    EXILE_SPEECH = '放逐发言'
    EXILE_VOTE = '放逐投票'
    EXILE_PK_SPEECH = '放逐PK发言'
    EXILE_PK_VOTE = '放逐PK投票'


# 玩家角色
class Role(PlainEnum):
    WOLF = '狼人'
    WOLF_KING = '狼王'
    SEER = '预言家'
    WITCH = '女巫'
    GUARD = '守卫'
    HUNTER = '猎人'
    CITIZEN = '平民'
    DREAMER = '摄梦人'
    IDIOT = '白痴'

    @classmethod
    def as_god_citizen_options(cls) -> list:
        return list(cls.god_citizen_mapping().keys())

    @classmethod
    def as_god_wolf_options(cls) -> list:
        return list(cls.god_wolf_mapping().keys())

    @classmethod
    def from_option(cls, option: Union[str, list]):
        if isinstance(option, list):
            return [cls.mapping()[item] for item in option]
        elif isinstance(option, str):
            return cls.mapping()[option]
        else:
            raise NotImplementedError

    @classmethod
    def normal_mapping(cls) -> dict:
        return {
            '狼人': cls.WOLF,
            '平民': cls.CITIZEN,
        }

    @classmethod
    def god_wolf_mapping(cls) -> dict:
        return {
            '狼王': cls.WOLF_KING
        }

    @classmethod
    def god_citizen_mapping(cls) -> dict:
        return {
            '预言家': cls.SEER,
            '女巫': cls.WITCH,
            '守卫': cls.GUARD,
            '猎人': cls.HUNTER,
            '摄梦人': cls.DREAMER,   # 新增
        }

    @classmethod
    def mapping(cls) -> dict:
        return dict(**cls.normal_mapping(), **cls.god_wolf_mapping(), **cls.god_citizen_mapping())


# === 女巫规则 ===
class WitchRule(Enum):
    SELF_RESCUE_FIRST_NIGHT_ONLY = '仅第一夜可自救'
    NO_SELF_RESCUE = '不可自救'
    ALWAYS_SELF_RESCUE = '始终可自救'

    @classmethod
    def as_options(cls) -> list:
        return list(cls.mapping().keys())

    @classmethod
    def from_option(cls, option: Union[str, list]):
        if isinstance(option, list):
            return [cls.mapping()[item] for item in option]
        elif isinstance(option, str):
            return cls.mapping()[option]
        else:
            raise NotImplementedError

    @classmethod
    def mapping(cls) -> dict:
        return {
            '仅第一夜可自救': cls.SELF_RESCUE_FIRST_NIGHT_ONLY,
            '始终可自救': cls.ALWAYS_SELF_RESCUE,
            '不可自救': cls.NO_SELF_RESCUE,
        }


# === 守卫规则 ===
class GuardRule(Enum):
    MED_CONFLICT = '同时被守被救时，对象死亡'
    NO_MED_CONFLICT = '同时被守被救时，对象存活'

    @classmethod
    def as_options(cls) -> list:
        return list(cls.mapping().keys())

    @classmethod
    def from_option(cls, option: Union[str, list]):
        if isinstance(option, list):
            return [cls.mapping()[item] for item in option]
        elif isinstance(option, str):
            return cls.mapping()[option]
        else:
            raise NotImplementedError

    @classmethod
    def mapping(cls) -> dict:
        return {
            '同时被守被救时，对象死亡': cls.MED_CONFLICT,
            '同时被守被救时，对象存活': cls.NO_MED_CONFLICT,
        }
