"""Runtime flow controllers for Room after the game starts."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Type

from enums import Role, PlayerStatus
from presets.base import WOLF_TEAM_ROLES, BaseGameConfig
from presets.game_config_registry import resolve_game_config_class
from roles.nine_tailed_fox import NineTailedFox
from roles.wolf_beauty import WolfBeauty
from models.runtime.daytime import DaytimeFlowMixin
from models.runtime.sheriff import SheriffFlowMixin
from models.runtime.tools import VoteTimer, BadgeTransferTimer
from . import logger

if TYPE_CHECKING:
    from models.room import Room


class RoomRuntimeMixin(SheriffFlowMixin, DaytimeFlowMixin):
    """Mixin containing gameplay logic that lives outside room.py."""

    def __post_init__(self):
        self._vote_timer = VoteTimer()
        self._badge_timer = BadgeTransferTimer()
        super_post = getattr(super(), "__post_init__", None)
        if callable(super_post):
            super_post()

    def _ensure_game_config(self) -> BaseGameConfig:
        """Instantiate the game flow handler when first accessed."""
        if getattr(self, '_game_config', None) is None:
            config_cls = self._resolve_game_config_class()
            self._game_config = config_cls(self)
        return self._game_config

    def _resolve_game_config_class(self) -> Type[BaseGameConfig]:
        return resolve_game_config_class(self.roles)

    async def game_loop(self):
        return await self._ensure_game_config().game_loop()

    async def night_logic(self):
        return await self._ensure_game_config().night_logic()

    def _has_active_role(self, roles: List[Role]) -> bool:
        return self._ensure_game_config().has_active_role(roles)

    def _has_configured_role(self, roles: List[Role]) -> bool:
        return self._ensure_game_config().has_configured_role(roles)

    def _ensure_half_blood_choices(self):
        self._ensure_game_config().ensure_half_blood_choices()

    async def wait_for_player(self):
        return await self._ensure_game_config().wait_for_player()

    async def check_game_end(self):
        return await self._ensure_game_config().check_game_end()

    async def end_game(self, reason: str):
        return await self._ensure_game_config().end_game(reason)

    def list_alive_players(self):
        return [u for u in self.players.values() if u.status == PlayerStatus.ALIVE]

    def list_pending_kill_players(self):
        """返回本夜被标记为待死亡（被狼人击中）的玩家列表"""
        return [u for u in self.players.values() if u.status == PlayerStatus.PENDING_DEAD]

    def get_active_wolves(self):
        return [
            u for u in self.players.values()
            if u.role in WOLF_TEAM_ROLES and u.status != PlayerStatus.DEAD
        ]

    async def vote_kill(self, nick: str):
        player = self.players.get(nick)
        if not player:
            return
        player.status = PlayerStatus.DEAD
        self.broadcast_msg(f"{nick} 被投票出局", tts=True)
        if player.role in (Role.HUNTER, Role.WOLF_KING) and player.skill.get('can_shoot', False):
            player.send_msg('你被投票出局，立即开枪！')
        # 处理狼美人殉情
        if player.role == Role.WOLF_BEAUTY:
            WolfBeauty.handle_wolf_beauty_death(self, player)
        self.update_nine_tailed_state()

    def _format_label(self, nick: str) -> str:
        player = self.players.get(nick)
        if not player:
            return nick
        seat = player.seat or '?'
        return f"{seat}号{player.nick}"

    def _is_alive(self, nick: str) -> bool:
        player = self.players.get(nick)
        return bool(player and player.status == PlayerStatus.ALIVE)

    def _alive_nicks(self) -> List[str]:
        return [u.nick for u in self.list_alive_players()]

    def _sheriff_pending_nicks(self) -> List[str]:
        pending = getattr(self, 'death_pending', []) or []
        return [nick for nick in pending if nick in self.players]

    def _sheriff_signup_pool(self) -> List[str]:
        alive = self._alive_nicks()
        extras = [nick for nick in self._sheriff_pending_nicks() if nick not in alive]
        return alive + extras

    def _is_sheriff_eligible(self, nick: str) -> bool:
        return self._is_alive(nick) or nick in self._sheriff_pending_nicks()

    def can_participate_in_sheriff(self, nick: str) -> bool:
        return self._is_sheriff_eligible(nick)

    def _can_player_vote(self, nick: str) -> bool:
        player = self.players.get(nick)
        if not player or player.status != PlayerStatus.ALIVE:
            return False
        if player.skill.get('idiot_vote_banned', False):
            return False
        return True

    def update_nine_tailed_state(self, include_pending: bool = False):
        """Refresh 九尾妖狐尾巴结算，必要时触发死亡。"""
        for user in self.players.values():
            inst = getattr(user, 'role_instance', None)
            if isinstance(inst, NineTailedFox):
                try:
                    inst.refresh_tail_state(include_pending=include_pending, register_death=True)
                except Exception:
                    logger.exception('九尾妖狐尾巴结算失败')
