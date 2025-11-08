# roles/citizen.py
from typing import Optional
from .base import RoleBase

class Citizen(RoleBase):
    name = '平民'
    team = '好人阵营'
    can_act_at_night = False
    can_act_at_day = False

    def should_act(self) -> bool:
        return False  # 平民无夜晚技能
