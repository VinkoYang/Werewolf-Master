# Wolf/roles.py
from enums import Role, GameStage
from models.room import Room
from models.user import User
import random

class DreamerMixin:
    """摄梦人技能混入类，所有摄梦人用户会自动拥有这两个 skill"""
    def __init__(self):
        self.skill.setdefault('last_dream_target', None)   # 上晚目标 nick
        self.skill.setdefault('curr_dream_target', None)   # 本晚目标 nick
        self.skill.setdefault('dreamer_nick', None)        # 当前被梦游的玩家 nick
        self.skill.setdefault('can_shoot', True)           # 猎人/狼王是否还能开枪

    def dreamer_select(self, nick: str):
        """摄梦人夜晚选择目标（在 UI 里调用）"""
        self.skill['curr_dream_target'] = nick

    def dreamer_random(self):
        """未选择时系统随机"""
        alive = [u.nick for u in self.room.list_alive_players() if u.nick != self.nick]
        if alive:
            self.skill['curr_dream_target'] = random.choice(alive)


def apply_dreamer_logic(room: Room):
    """
    夜晚结束后统一结算摄梦人技能
    1. 免疫夜间伤害（狼刀、毒）
    2. 连续两晚同一人 → 死亡
    3. 摄梦人死亡 → 梦游者同死
    """
    dreamer_user: User | None = next((u for u in room.players.values() if u.role == Role.DREAMER), None)
    if not dreamer_user:
        return

    # 1. 未手动选择 → 随机
    if dreamer_user.skill['curr_dream_target'] is None:
        dreamer_user.dreamer_random()

    target_nick = dreamer_user.skill['curr_dream_target']
    target_user = room.players.get(target_nick)
    if not target_user:
        return

    # 2. 连续两晚同一人 → 死亡（梦游死亡）
    if dreamer_user.skill['last_dream_target'] == target_nick:
        target_user.status = PlayerStatus.DEAD
        room.broadcast_msg(f"{target_nick} 因连续两晚被摄梦而死亡", tts=True)
        # 梦游死亡 → 猎人/狼王不能开枪
        if target_user.role in (Role.HUNTER, Role.WOLF_KING):
            target_user.skill['can_shoot'] = False
        # 清除当前梦游标记
        dreamer_user.skill['dreamer_nick'] = None
    else:
        # 正常梦游 → 夜间免疫
        dreamer_user.skill['dreamer_nick'] = target_nick
        # 给目标加临时免疫标记（后面 check_result 会读取）
        target_user.skill['dream_immunity'] = True

    # 3. 保存本晚为上晚
    dreamer_user.skill['last_dream_target'] = target_nick
    dreamer_user.skill['curr_dream_target'] = None

    # 4. 若摄梦人本晚已死 → 梦游者同死
    if dreamer_user.status == PlayerStatus.DEAD and dreamer_user.skill['dreamer_nick']:
        dream_nick = dreamer_user.skill['dreamer_nick']
        dream_u = room.players.get(dream_nick)
        if dream_u and dream_u.status != PlayerStatus.DEAD:
            dream_u.status = PlayerStatus.DEAD
            room.broadcast_msg(f"摄梦人死亡，梦游者 {dream_nick} 随之出局", tts=True)
            if dream_u.role in (Role.HUNTER, Role.WOLF_KING):
                dream_u.skill['can_shoot'] = False
