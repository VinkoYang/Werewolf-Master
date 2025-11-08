# roles/wolf_king.py
from typing import Optional
from .wolf import Wolf

class WolfKing(Wolf):
    name = '狼王'
    team = '狼人阵营'
    can_act_at_night = True
    can_act_at_day = False

    # 如果狼王有额外技能（如死亡射击），在这里添加
    pass
