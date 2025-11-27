from __future__ import annotations

import asyncio
import random
from typing import List, TYPE_CHECKING

from enums import GameStage, LogCtrl, PlayerStatus, Role
from models import logger

if TYPE_CHECKING:
    from models.room import Room

WOLF_TEAM_ROLES = {Role.WOLF, Role.WOLF_KING, Role.WHITE_WOLF_KING, Role.NIGHTMARE}


class BaseGameConfig:
    """Base interface for orchestrating a Werewolf match."""

    def __init__(self, room: Room):
        self.room = room

    async def game_loop(self):  # pragma: no cover
        raise NotImplementedError

    async def night_logic(self):  # pragma: no cover
        raise NotImplementedError

    async def wait_for_player(self):  # pragma: no cover
        raise NotImplementedError

    async def check_game_end(self):  # pragma: no cover
        raise NotImplementedError

    async def end_game(self, reason: str):  # pragma: no cover
        raise NotImplementedError


class DefaultGameFlow(BaseGameConfig):
    """Default implementation shared by most board presets."""

    async def game_loop(self):
        room = self.room
        while not room.game_over:
            if not room.started:
                await asyncio.sleep(1)
                continue
            await self.night_logic()
            await self.check_game_end()
            await asyncio.sleep(1)
        room.logic_thread = None

    async def night_logic(self):
        room = self.room
        logger.info(f"=== 第 {room.round + 1} 夜 开始 ===")
        room.round += 1
        room.broadcast_msg(f"============ 第 {room.round} 晚 ============")
        room.broadcast_msg('天黑请闭眼', tts=True)
        await asyncio.sleep(3)

        await self.run_pre_wolf_phase()
        await self.run_wolf_stage()
        await self.run_post_wolf_stages()

        dreamer = next((u for u in room.players.values() if u.role == Role.DREAMER and u.status == PlayerStatus.ALIVE), None)
        if dreamer:
            dreamer.role_instance.apply_logic(room)

        dead_this_night = []
        for u in room.players.values():
            if u.status == PlayerStatus.DEAD:
                continue
            immunity = u.skill.get('dream_immunity', False)
            u.skill['dream_immunity'] = False
            dream_cause = u.skill.pop('dream_forced_death', None)

            if dream_cause:
                u.status = PlayerStatus.DEAD
                dead_this_night.append(u.nick)
                if u.role in (Role.HUNTER, Role.WOLF_KING):
                    u.skill['can_shoot'] = False
                    u.send_msg('你无法开枪。')
                u.skill.pop('dreamer_nick', None)
                continue

            if u.status == PlayerStatus.PENDING_POISON:
                if not immunity:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)
                    if u.role == Role.HUNTER:
                        u.skill['can_shoot'] = False
                else:
                    u.status = PlayerStatus.ALIVE

            elif u.status == PlayerStatus.PENDING_DEAD:
                if immunity or u.status in (PlayerStatus.PENDING_HEAL, PlayerStatus.PENDING_GUARD):
                    u.status = PlayerStatus.ALIVE
                else:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)

            elif u.status in (PlayerStatus.PENDING_HEAL, PlayerStatus.PENDING_GUARD):
                u.status = PlayerStatus.ALIVE
            else:
                u.status = PlayerStatus.ALIVE

        dreamers = [user for user in room.players.values() if user.role == Role.DREAMER]
        if dreamers:
            for dreamer_player in dreamers:
                for candidate in room.players.values():
                    if candidate.skill.get('dreamer_nick') != dreamer_player.nick:
                        continue
                    if dreamer_player.status == PlayerStatus.DEAD and candidate.status != PlayerStatus.DEAD:
                        candidate.status = PlayerStatus.DEAD
                        dead_this_night.append(candidate.nick)
                        if candidate.role in (Role.HUNTER, Role.WOLF_KING):
                            candidate.skill['can_shoot'] = False
                            candidate.send_msg('你无法开枪。')
                    candidate.skill.pop('dreamer_nick', None)

        room.death_pending = dead_this_night
        room.update_nine_tailed_state()
        
        # 清除梦魇的恐惧效果
        self._clear_nightmare_fear_effects()
        
        room.broadcast_msg('天亮请睁眼', tts=True)
        await asyncio.sleep(2)
        needs_sheriff_phase = False
        if not room.sheriff_badge_destroyed:
            if room.round == 1:
                needs_sheriff_phase = True
            elif room.skill.get('sheriff_deferred_active'):
                needs_sheriff_phase = True

        if needs_sheriff_phase:
            room.stage = GameStage.SHERIFF
            if room.skill.get('sheriff_deferred_active'):
                room.broadcast_msg('继续未完成的警长竞选', tts=True)
                room.resume_deferred_sheriff_phase()
            else:
                room.broadcast_msg('进行警上竞选', tts=True)
                room.init_sheriff_phase()
            while room.sheriff_state.get('phase') != 'done':
                await asyncio.sleep(0.5)
        else:
            room.prepare_day_phase()

        while not room.day_state:
            await asyncio.sleep(0.2)
        while room.day_state.get('phase') != 'done':
            await asyncio.sleep(0.5)

    async def run_pre_wolf_phase(self):
        await self._run_nightmare_stage_if_needed()
        await self.handle_custom_pre_wolf_stages()
        await self._run_half_blood_stage_if_needed()

    async def handle_custom_pre_wolf_stages(self):
        """Hook for subclasses to execute stages before狼队行动。"""

    async def _run_nightmare_stage_if_needed(self):
        """梦魇单独睁眼阶段（先于狼人行动）"""
        room = self.room
        if not self.has_configured_role([Role.NIGHTMARE]):
            return

        # 先清理所有倒计时任务，避免遗留任务干扰
        for user in room.players.values():
            task = user.skill.pop('countdown_task', None)
            if task:
                task.cancel()
        
        room.stage = GameStage.NIGHTMARE
        
        # 立即设置 waiting 状态，防止玩家输入循环启动新的倒计时
        if self.has_active_role([Role.NIGHTMARE]):
            room.waiting = True
        
        for user in room.players.values():
            user.skill['acted_this_stage'] = False

        room.broadcast_msg('梦魇请睁眼', tts=True)
        await asyncio.sleep(1)

        if self.has_active_role([Role.NIGHTMARE]):
            await self.wait_for_player()
        else:
            await asyncio.sleep(5)

        await asyncio.sleep(1)
        room.broadcast_msg('梦魇请闭眼', tts=True)
        await asyncio.sleep(2)

    async def _run_half_blood_stage_if_needed(self):
        room = self.room
        if room.round != 1 or not self.has_configured_role([Role.HALF_BLOOD]):
            return
        room.stage = GameStage.HALF_BLOOD
        for user in room.players.values():
            user.skill['acted_this_stage'] = False
            if user.role in WOLF_TEAM_ROLES:
                user.skill['wolf_action_done'] = False
        room.broadcast_msg('混血儿请出现', tts=True)
        await asyncio.sleep(1)

        if self.has_active_role([Role.HALF_BLOOD]):
            room.waiting = True
            await self.wait_for_player()
            self.ensure_half_blood_choices()
        else:
            await asyncio.sleep(5)

        await asyncio.sleep(1)
        room.broadcast_msg('混血儿请闭眼', tts=True)
        await asyncio.sleep(2)

    async def run_wolf_stage(self):
        room = self.room
        room.stage = GameStage.WOLF
        for user in room.players.values():
            user.skill['acted_this_stage'] = False
            if user.role in WOLF_TEAM_ROLES:
                user.skill['wolf_action_done'] = False
        room.broadcast_msg('狼人请出现', tts=True)

        wolf_players = room.get_active_wolves()
        if wolf_players:
            labels = [room._format_label(u.nick) for u in wolf_players]
            wolf_info = "狼人玩家是：" + "、".join(labels)
            wolf_king = next((u for u in wolf_players if u.role == Role.WOLF_KING), None)
            if wolf_king:
                wolf_info += f"，狼王是：{room._format_label(wolf_king.nick)}"
            white_king = next((u for u in wolf_players if u.role == Role.WHITE_WOLF_KING), None)
            if white_king:
                wolf_info += f"，白狼王是：{room._format_label(white_king.nick)}"
            nightmare = next((u for u in wolf_players if u.role == Role.NIGHTMARE), None)
            if nightmare:
                wolf_info += f"，梦魇是：{room._format_label(nightmare.nick)}"

            for u in wolf_players:
                room.send_msg(wolf_info, nick=u.nick)

        await asyncio.sleep(2)

        # 检查是否被梦魇恐惧导致狼队空刀
        wolf_forced_empty = room.skill.get('wolf_forced_empty_knife', False)

        if wolf_players and not wolf_forced_empty:
            room.waiting = True
            await self.wait_for_player()
        elif wolf_forced_empty:
            # 梦魇恐惧狼队友导致空刀，通知狼队
            for u in room.players.values():
                if u.role in WOLF_TEAM_ROLES:
                    room.send_msg("梦魇恐惧了狼队友，今夜狼队空刀。", nick=u.nick)
            await asyncio.sleep(3)
        else:
            await asyncio.sleep(1)

        wolf_votes = room.skill.get('wolf_votes', {})
        if wolf_players and wolf_votes and not wolf_forced_empty:
            counts = {t: len(voters) for t, voters in wolf_votes.items()}
            max_count = max(counts.values())
            candidates = [t for t, c in counts.items() if c == max_count]
            chosen = candidates[0] if len(candidates) == 1 else random.choice(candidates)
            target = room.players.get(chosen)
            if target and target.status == PlayerStatus.ALIVE:
                target.status = PlayerStatus.PENDING_DEAD

            target_seat = target.seat if target else '?'
            for u in room.players.values():
                if u.role in WOLF_TEAM_ROLES:
                    room.send_msg(f"今夜，狼队选择{target_seat}号玩家被击杀。", nick=u.nick)

            room.skill.pop('wolf_votes', None)
            for u in room.players.values():
                u.skill.pop('wolf_choice', None)
                u.skill.pop('wolf_action_done', None)
        elif wolf_players:
            for u in room.players.values():
                if u.role in WOLF_TEAM_ROLES:
                    room.send_msg("今夜，狼队空刀。", nick=u.nick)
                    u.skill.pop('wolf_action_done', None)

        await asyncio.sleep(3)
        room.broadcast_msg('狼人请闭眼', tts=True)
        await asyncio.sleep(2)

    async def run_post_wolf_stages(self):
        for stage, role_list in self.night_role_order():
            await self.run_role_stage(stage, role_list)

    def night_role_order(self) -> List[tuple]:
        return [
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.DREAMER, [Role.DREAMER]),
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.HUNTER, [Role.HUNTER]),
            (GameStage.WOLF_KING, [Role.WOLF_KING]),
        ]

    async def run_role_stage(self, stage: GameStage, role_list: List[Role]) -> bool:
        room = self.room
        if not self.has_configured_role(role_list):
            return False

        room.stage = stage
        for user in room.players.values():
            user.skill['acted_this_stage'] = False

        room.broadcast_msg(f'{stage.value}请出现', tts=True)
        await asyncio.sleep(1)

        if self.has_active_role(role_list):
            room.waiting = True
            await self.wait_for_player()
        else:
            await asyncio.sleep(self.stage_idle_delay(stage))

        await asyncio.sleep(1)
        room.broadcast_msg(f'{stage.value}请闭眼', tts=True)
        await asyncio.sleep(2)
        return True

    def stage_idle_delay(self, stage: GameStage) -> float:
        return 20

    def has_active_role(self, roles: List[Role]) -> bool:
        room = self.room
        alive_statuses = {PlayerStatus.ALIVE, PlayerStatus.PENDING_GUARD, PlayerStatus.PENDING_HEAL}
        return any(
            user.role in roles and user.status in alive_statuses
            for user in room.players.values()
        )

    def has_configured_role(self, roles: List[Role]) -> bool:
        room = self.room
        return any(role in room.roles for role in roles) or any(
            user.role in roles for user in room.players.values()
        )

    def _clear_nightmare_fear_effects(self):
        """清除所有玩家的梦魇恐惧状态"""
        room = self.room
        for player in room.players.values():
            player.skill.pop('feared_this_night', None)
            player.skill.pop('feared_by', None)
        room.skill.pop('wolf_forced_empty_knife', None)

    def ensure_half_blood_choices(self):
        room = self.room
        for user in room.players.values():
            if user.role != Role.HALF_BLOOD or user.status == PlayerStatus.DEAD:
                continue
            role_inst = getattr(user, 'role_instance', None)
            if role_inst and hasattr(role_inst, 'ensure_choice'):
                try:
                    role_inst.ensure_choice()
                except Exception:
                    logger.exception('混血儿认亲结算失败')

    async def wait_for_player(self):
        room = self.room
        timeout = 20
        loop = asyncio.get_event_loop()
        start = loop.time()
        while room.waiting:
            if loop.time() - start > timeout:
                room.waiting = False
                room.broadcast_msg("行动超时，系统自动跳过", tts=True)
                break
            await asyncio.sleep(0.1)

        for user in room.players.values():
            try:
                task = user.skill.pop('countdown_task', None)
                if task:
                    task.cancel()
            except Exception:
                pass
        room.log.append((None, LogCtrl.RemoveInput))

    async def check_game_end(self):
        room = self.room
        alive = room.list_alive_players()
        wolves = [u for u in alive if u.role in WOLF_TEAM_ROLES]
        goods = [u for u in alive if u.role not in WOLF_TEAM_ROLES]
        half_bloods = [u for u in goods if u.role == Role.HALF_BLOOD]
        for hb in half_bloods:
            if hb.skill.get('half_blood_camp', 'good') == 'wolf':
                goods = [g for g in goods if g.nick != hb.nick]
                wolves.append(hb)
        if not wolves:
            await self.end_game("好人阵营获胜！狼人全部出局")
        elif len(wolves) >= len(goods):
            await self.end_game("狼人阵营获胜！好人被屠光")

    async def end_game(self, reason: str):
        room = self.room
        if room.game_over:
            return
        room.game_over = True
        room.started = False
        room.stage = None
        room.broadcast_msg(f"游戏结束，{reason}。", tts=True)
        await asyncio.sleep(2)
        for nick, user in room.players.items():
            room.broadcast_msg(f"{nick}：{user.role_instance.name if user.role_instance else '无'}", tts=True)
            user.role = None
            user.role_instance = None
            user.status = None
            user.skill.clear()
        logger.info(f"房间 {room.id} 游戏结束：{reason}")
