# roles/dreamer.py
import random
from typing import Optional
from .base import RoleBase, player_action
from enums import PlayerStatus, GameStage

class Dreamer(RoleBase):
    name = '摄梦人'
    team = '好人阵营'
    can_act_at_night = True
    can_act_at_day = False

    def should_act(self) -> bool:
        room = self.user.room
        return self.user.status != PlayerStatus.DEAD and room.stage == GameStage.DREAMER

    @player_action
    def select_target(self, nick: str) -> Optional[str]:
        if nick == self.user.nick:
            return '不能选择自己'
        target = self.user.room.players.get(nick)
        if not target or target.status == PlayerStatus.DEAD:
            return '目标已死亡'
        self.user.skill['curr_dream_target'] = nick
        return True

    def apply_logic(self, room) -> None:
        """
        夜晚结束后统一结算摄梦人技能
        1. 免疫夜间伤害（狼刀、毒）
        2. 连续两晚同一人 → 死亡
        3. 摄梦人死亡 → 梦游者同死
        """
        if self.user.skill['curr_dream_target'] is None:
            alive = [u.nick for u in room.list_alive_players() if u.nick != self.user.nick]
            if alive:
                self.user.skill['curr_dream_target'] = random.choice(alive)

        target_nick = self.user.skill['curr_dream_target']
        target_user = room.players.get(target_nick)
        if not target_user:
            return

        if self.user.skill['last_dream_target'] == target_nick:
            target_user.status = PlayerStatus.DEAD
            room.broadcast_msg(f"{target_nick} 因连续两晚被摄梦而死亡", tts=True)
            if target_user.role in (Role.HUNTER, Role.WOLF_KING):
                target_user.skill['can_shoot'] = False
            self.user.skill['dreamer_nick'] = None
        else:
            self.user.skill['dreamer_nick'] = target_nick
            target_user.skill['dream_immunity'] = True

        self.user.skill['last_dream_target'] = target_nick
        self.user.skill['curr_dream_target'] = None

        if self.user.status == PlayerStatus.DEAD and self.user.skill['dreamer_nick']:
            dream_nick = self.user.skill['dreamer_nick']
            dream_u = room.players.get(dream_nick)
            if dream_u and dream_u.status != PlayerStatus.DEAD:
                dream_u.status = PlayerStatus.DEAD
                room.broadcast_msg(f"摄梦人死亡，梦游者 {dream_nick} 随之出局", tts=True)
                if dream_u.role in (Role.HUNTER, Role.WOLF_KING):
                    dream_u.skill['can_shoot'] = False
