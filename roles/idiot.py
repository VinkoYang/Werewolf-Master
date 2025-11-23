# roles/idiot.py
from .base import RoleBase

class Idiot(RoleBase):
    name = '白痴'
    team = '好人阵营'
    can_act_at_night = False
    can_act_at_day = False
    needs_global_confirm = False

    def supports_last_skill(self) -> bool:
        return False
