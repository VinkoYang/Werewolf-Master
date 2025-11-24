# models/room.py
import asyncio
import random
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Union, Any

from pywebio.session import run_async
from pywebio.session.coroutinebased import TaskHandler

from enums import Role, WitchRule, GuardRule, SheriffBombRule, GameStage, LogCtrl, PlayerStatus
from models.system import Global, Config
from models.user import User
from utils import say
from . import logger

# ---------- 角色类 ----------
from roles.citizen import Citizen
from roles.wolf import Wolf
from roles.wolf_king import WolfKing
from roles.seer import Seer
from roles.witch import Witch
from roles.guard import Guard
from roles.hunter import Hunter
from roles.dreamer import Dreamer
from roles.idiot import Idiot
from roles.half_blood import HalfBlood
from roles.white_wolf_king import WhiteWolfKing

role_classes = {
    Role.CITIZEN: Citizen,
    Role.WOLF: Wolf,
    Role.WOLF_KING: WolfKing,
    Role.WHITE_WOLF_KING: WhiteWolfKing,
    Role.SEER: Seer,
    Role.WITCH: Witch,
    Role.GUARD: Guard,
    Role.HUNTER: Hunter,
    Role.DREAMER: Dreamer,
    Role.IDIOT: Idiot,
    Role.HALF_BLOOD: HalfBlood,
}

WOLF_TEAM_ROLES = {Role.WOLF, Role.WOLF_KING, Role.WHITE_WOLF_KING}

@dataclass
class Room:
    id: Optional[int] = None
    roles: List[Role] = field(default_factory=list)
    witch_rule: WitchRule = WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY
    guard_rule: GuardRule = GuardRule.MED_CONFLICT
    sheriff_bomb_rule: SheriffBombRule = SheriffBombRule.DOUBLE_LOSS

    started: bool = False
    roles_pool: List[Role] = field(default_factory=list)
    players: Dict[str, User] = field(default_factory=dict)
    round: int = 0
    stage: Optional[GameStage] = None
    waiting: bool = False
    log: List[Tuple[Union[str, None], Union[str, LogCtrl]]] = field(default_factory=list)
    skill: Dict[str, Any] = field(default_factory=dict)  # 用于狼人击杀等

    logic_thread: Optional[TaskHandler] = None
    game_over: bool = False

    death_pending: List[str] = field(default_factory=list)
    current_speaker: Optional[str] = None
    sheriff_speakers: Optional[List[str]] = None
    sheriff_speaker_index: int = 0
    sheriff_state: Dict[str, Any] = field(default_factory=dict)
    day_state: Dict[str, Any] = field(default_factory=dict)
    sheriff_badge_destroyed: bool = False

    async def start_game(self):
        if self.started or len(self.players) < len(self.roles):
            return
        self.started = True
        self.game_over = False
        # 在游戏开始时添加公共隔断，提升可读性
        self.broadcast_msg('=' * 22)
        self.broadcast_msg("游戏开始！身份发放中...", tts=True)
        await asyncio.sleep(2)

        random.shuffle(self.roles_pool)
        for user in self.players.values():
            role_enum = self.roles_pool.pop()
            user.role = role_enum
            user.role_instance = role_classes[role_enum](user)
            user.status = PlayerStatus.ALIVE
            if user.role == Role.WITCH:
                user.skill['heal'] = user.skill['poison'] = True
            if user.role in (Role.HUNTER, Role.WOLF_KING):
                user.skill['can_shoot'] = True
            user.send_msg(f"你的身份是：{user.role_instance.name}")

        for idx, user in enumerate(self.players.values(), 1):
            user.seat = idx

        seat_msg = "座位表： " + " | ".join(f"{u.seat}号: {u.nick}" for u in sorted(self.players.values(), key=lambda x: x.seat))
        self.broadcast_msg(seat_msg, tts=False)
        await asyncio.sleep(3)

        if not self.logic_thread:
            self.logic_thread = run_async(self.game_loop())
        self.waiting = False

    async def game_loop(self):
        while not self.game_over:
            if not self.started:
                await asyncio.sleep(1); continue
            await self.night_logic()
            await self.check_game_end()
            await asyncio.sleep(1)
        self.logic_thread = None

    async def night_logic(self):
        logger.info(f"=== 第 {self.round + 1} 夜 开始 ===")
        self.round += 1
        # 在天黑提示前加上夜数隔断，便于在 Public 区分每晚开始
        self.broadcast_msg(f"============ 第 {self.round} 晚 ============")
        self.broadcast_msg('天黑请闭眼', tts=True)
        await asyncio.sleep(3)

        # ---------- 混血儿第一夜认亲 ----------
        if self.round == 1 and self._has_configured_role([Role.HALF_BLOOD]):
            self.stage = GameStage.HALF_BLOOD
            for user in self.players.values():
                user.skill['acted_this_stage'] = False
            self.broadcast_msg('混血儿请出现', tts=True)
            await asyncio.sleep(1)

            if self._has_active_role([Role.HALF_BLOOD]):
                self.waiting = True
                await self.wait_for_player()
                self._ensure_half_blood_choices()
            else:
                await asyncio.sleep(5)

            await asyncio.sleep(1)
            self.broadcast_msg('混血儿请闭眼', tts=True)
            await asyncio.sleep(2)

        # ---------- 狼人 ----------
        self.stage = GameStage.WOLF
        for user in self.players.values():
            user.skill['acted_this_stage'] = False
        self.broadcast_msg('狼人请出现', tts=True)
        
        # 发送狼队成员信息给所有可行动的狼人
        wolf_players = self.get_active_wolves()
        if wolf_players:
            labels = [self._format_label(u.nick) for u in wolf_players]
            wolf_info = "狼人玩家是：" + "、".join(labels)
            wolf_king = next((u for u in wolf_players if u.role == Role.WOLF_KING), None)
            if wolf_king:
                wolf_info += f"，狼王是：{self._format_label(wolf_king.nick)}"
            white_king = next((u for u in wolf_players if u.role == Role.WHITE_WOLF_KING), None)
            if white_king:
                wolf_info += f"，白狼王是：{self._format_label(white_king.nick)}"

            for u in wolf_players:
                self.send_msg(wolf_info, nick=u.nick)

        await asyncio.sleep(2)

        if wolf_players:
            self.waiting = True
            await self.wait_for_player()
        else:
            await asyncio.sleep(1)

        # 统一结算狼人击杀（统计票数，最多票者为今晚被刀）
        wolf_votes = self.skill.get('wolf_votes', {})
        kill_target = None
        
        if wolf_players and wolf_votes:
            # 计算每个目标的票数
            counts = {t: len(voters) for t, voters in wolf_votes.items()}
            max_count = max(counts.values())
            candidates = [t for t, c in counts.items() if c == max_count]
            
            # 根据需求3判断：
            # a. 单个玩家 -> 直接选择
            # b. 多个玩家最多票 -> 选择最多票的
            # c. 平票 -> 随机选择
            if len(candidates) == 1:
                chosen = candidates[0]
            else:
                # 平票情况，随机选择
                chosen = random.choice(candidates)
            
            target = self.players.get(chosen)
            if target and target.status == PlayerStatus.ALIVE:
                target.status = PlayerStatus.PENDING_DEAD
                kill_target = target
            
            # 发送击杀结果给所有狼人（包括未行动的狼人）
            target_seat = target.seat if target else '?'
            for u in self.players.values():
                if u.role in WOLF_TEAM_ROLES:
                    self.send_msg(f"今夜，狼队选择{target_seat}号玩家被击杀。", nick=u.nick)
            
            # 清理投票记录
            if 'wolf_votes' in self.skill:
                del self.skill['wolf_votes']
            # 清理玩家临时选择
            for u in self.players.values():
                u.skill.pop('wolf_choice', None)
        elif wolf_players:
            # d. 所有狼人都没有选择或点击了"放弃" -> 空刀
            for u in self.players.values():
                if u.role in WOLF_TEAM_ROLES:
                    self.send_msg("今夜，狼队空刀。", nick=u.nick)

        # 延迟3秒后再显示"狼人请闭眼"
        await asyncio.sleep(3)
        self.broadcast_msg('狼人请闭眼', tts=True)
        await asyncio.sleep(2)

        # ---------- 其他神职 ----------
        night_roles = [
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.DREAMER, [Role.DREAMER]),
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.HUNTER, [Role.HUNTER]),
            (GameStage.WOLF_KING, [Role.WOLF_KING]),
        ]

        for stage, role_list in night_roles:
            if not self._has_configured_role(role_list):
                continue

            self.stage = stage
            for user in self.players.values():
                user.skill['acted_this_stage'] = False

            self.broadcast_msg(f'{stage.value}请出现', tts=True)
            await asyncio.sleep(1)

            # 女巫阶段：发送私聊信息给女巫
            if stage == GameStage.WITCH:
                for u in self.players.values():
                    if u.role == Role.WITCH and u.status == PlayerStatus.ALIVE:
                        # 女巫睡眼信息将在 get_actions 中发送（显示今夜被杀信息）
                        pass

            if self._has_active_role(role_list):
                self.waiting = True
                await self.wait_for_player()
            else:
                # 无对应在场角色，仍保留夜间阶段的等待时间
                await asyncio.sleep(20)

            await asyncio.sleep(1)
            self.broadcast_msg(f'{stage.value}请闭眼', tts=True)
            await asyncio.sleep(2)

        # ---------- 摄梦人结算 ----------
        dreamer = next((u for u in self.players.values() if u.role == Role.DREAMER and u.status == PlayerStatus.ALIVE), None)
        if dreamer:
            dreamer.role_instance.apply_logic(self)

        # ---------- 夜晚死亡结算 ----------
        dead_this_night = []
        for u in self.players.values():
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

        dreamers = [user for user in self.players.values() if user.role == Role.DREAMER]
        if dreamers:
            for dreamer_player in dreamers:
                for candidate in self.players.values():
                    if candidate.skill.get('dreamer_nick') != dreamer_player.nick:
                        continue
                    if dreamer_player.status == PlayerStatus.DEAD and candidate.status != PlayerStatus.DEAD:
                        candidate.status = PlayerStatus.DEAD
                        dead_this_night.append(candidate.nick)
                        if candidate.role in (Role.HUNTER, Role.WOLF_KING):
                            candidate.skill['can_shoot'] = False
                            candidate.send_msg('你无法开枪。')
                    candidate.skill.pop('dreamer_nick', None)

        self.death_pending = dead_this_night
        self.broadcast_msg('天亮请睁眼', tts=True)
        await asyncio.sleep(2)
        needs_sheriff_phase = False
        if not self.sheriff_badge_destroyed:
            if self.round == 1:
                needs_sheriff_phase = True
            elif self.skill.get('sheriff_deferred_active'):
                needs_sheriff_phase = True

        if needs_sheriff_phase:
            self.stage = GameStage.SHERIFF
            if self.skill.get('sheriff_deferred_active'):
                self.broadcast_msg('继续未完成的警长竞选', tts=True)
                self.resume_deferred_sheriff_phase()
            else:
                self.broadcast_msg('进行警上竞选', tts=True)
                self.init_sheriff_phase()
            while self.sheriff_state.get('phase') != 'done':
                await asyncio.sleep(0.5)
        else:
            self.prepare_day_phase()

        while not self.day_state:
            await asyncio.sleep(0.2)
        while self.day_state.get('phase') != 'done':
            await asyncio.sleep(0.5)

    def _has_active_role(self, roles: List[Role]) -> bool:
        alive_statuses = {PlayerStatus.ALIVE, PlayerStatus.PENDING_GUARD, PlayerStatus.PENDING_HEAL}
        return any(
            user.role in roles and user.status in alive_statuses
            for user in self.players.values()
        )

    def _has_configured_role(self, roles: List[Role]) -> bool:
        return any(role in self.roles for role in roles) or any(
            user.role in roles for user in self.players.values()
        )

    def _ensure_half_blood_choices(self):
        for user in self.players.values():
            if user.role != Role.HALF_BLOOD or user.status == PlayerStatus.DEAD:
                continue
            role_inst = getattr(user, 'role_instance', None)
            if role_inst and hasattr(role_inst, 'ensure_choice'):
                try:
                    role_inst.ensure_choice()
                except Exception:
                    logger.exception('混血儿认亲结算失败')

    async def wait_for_player(self):
        timeout = 20
        start = asyncio.get_event_loop().time()
        while self.waiting:
            if asyncio.get_event_loop().time() - start > timeout:
                self.waiting = False
                self.broadcast_msg("行动超时，系统自动跳过", tts=True)
                break
            await asyncio.sleep(0.1)
        
        # 阶段结束后，刷新所有玩家的界面（取消操作窗口）
        for user in self.players.values():
            try:
                # 取消倒计时任务
                task = user.skill.pop('countdown_task', None)
                if task:
                    task.cancel()
            except Exception:
                pass
        # 通过日志控制项通知所有会话关闭输入窗口
        self.log.append((None, LogCtrl.RemoveInput))

    async def check_game_end(self):
        alive = self.list_alive_players()
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
        if self.game_over: return
        self.game_over = True
        self.started = False
        self.stage = None
        self.broadcast_msg(f"游戏结束，{reason}。", tts=True)
        await asyncio.sleep(2)
        for nick, user in self.players.items():
            self.broadcast_msg(f"{nick}：{user.role_instance.name if user.role_instance else '无'}", tts=True)
            user.role = user.role_instance = user.status = None
            user.skill.clear()
        logger.info(f"房间 {self.id} 游戏结束：{reason}")

    def list_alive_players(self) -> List[User]:
        return [u for u in self.players.values() if u.status == PlayerStatus.ALIVE]

    def list_pending_kill_players(self) -> List[User]:
        """返回本夜被标记为待死亡（被狼人击中）的玩家列表"""
        return [u for u in self.players.values() if u.status == PlayerStatus.PENDING_DEAD]

    def get_active_wolves(self) -> List[User]:
        return [
            u for u in self.players.values()
            if u.role in WOLF_TEAM_ROLES and u.status != PlayerStatus.DEAD
        ]

    def is_full(self) -> bool:
        return len(self.players) >= len(self.roles)

    def add_player(self, user: 'User'):
        if user.room or user.nick in self.players: raise AssertionError
        self.players[user.nick] = user
        user.room = self
        user.start_syncer()
        user.seat = len(self.players)
        status = f'【{user.nick}】进入房间，人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}'
        user.game_msg.append(status)
        self.broadcast_msg(status)
        logger.info(f'用户 "{user.nick}" 加入房间 "{self.id}"，座位 {user.seat}')

    def remove_player(self, user: 'User'):
        if user.nick not in self.players: raise AssertionError
        self.players.pop(user.nick)
        user.stop_syncer()
        user.room = None
        if not self.players:
            Global.remove_room(self.id)
            return
        self.broadcast_msg(f'人数 {len(self.players)}/{len(self.roles)}，房主是 {self.get_host().nick}')
        logger.info(f'用户 "{user.nick}" 离开房间 "{self.id}"')

    def get_host(self) -> User:
        return next(iter(self.players.values())) if self.players else None

    def send_msg(self, text: str, nick: str):
        self.log.append((nick, text))

    def broadcast_msg(self, text: str, tts: bool = False):
        if tts: say(text)
        self.log.append((Config.SYS_NICK, text))

    def desc(self) -> str:
        return f'房间号 {self.id}，需要玩家 {len(self.roles)} 人，人员配置：{dict(Counter(self.roles))}'

    async def vote_kill(self, nick: str):
        player = self.players.get(nick)
        if not player: return
        player.status = PlayerStatus.DEAD
        self.broadcast_msg(f"{nick} 被投票出局", tts=True)
        if player.role in (Role.HUNTER, Role.WOLF_KING) and player.skill.get('can_shoot', False):
            player.send_msg('你被投票出局，立即开枪！')

    # -------------------- 警长阶段逻辑 --------------------
    def init_sheriff_phase(self):
        state = {
            'phase': 'signup',
            'up': [],
            'down': [],
            'withdrawn': [],
            'order_dir': None,
            'speech_queue': [],
            'pk_order_dir': None,
            'pk_candidates': [],
            'eligible_voters': [],
            'vote_records': {},
        }
        self.sheriff_state = state
        for user in self.players.values():
            user.skill['sheriff_vote'] = None
            user.skill['sheriff_voted'] = False
            user.skill['sheriff_withdrawn'] = False
            user.skill['sheriff_has_balloted'] = False
            user.skill['sheriff_vote_pending'] = False
            user.skill['sheriff_ballot_choice'] = None

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

    def _format_label(self, nick: str) -> str:
        player = self.players.get(nick)
        if not player:
            return nick
        seat = player.seat or '?'
        return f"{seat}号{player.nick}"

    def can_wolf_self_bomb(self, user: User) -> bool:
        if not user or user.status != PlayerStatus.ALIVE:
            return False
        if user.role not in WOLF_TEAM_ROLES:
            return False
        if self.stage == GameStage.SPEECH:
            state = self.sheriff_state or {}
            if state.get('phase') in ('speech', 'pk_speech'):
                return True
            return False
        if self.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
            day_phase = (self.day_state or {}).get('phase')
            return day_phase in ('exile_speech', 'exile_pk_speech')
        state = self.sheriff_state or {}
        if self.stage == GameStage.SHERIFF and state.get('phase') == 'deferred_withdraw':
            return True
        return False

    def get_active_sheriff_candidates(self) -> List[str]:
        state = self.sheriff_state or {}
        if not state:
            return []
        base = state.get('pk_candidates') if state.get('phase') in ('pk_speech', 'await_pk_vote', 'pk_vote') and state.get('pk_candidates') else state.get('up', [])
        active = [nick for nick in base if nick not in state.get('withdrawn', []) and self._is_sheriff_eligible(nick)]
        return active

    def record_sheriff_choice(self, user: User, choice: str):
        state = self.sheriff_state or {}
        if state.get('phase') != 'signup':
            return
        if choice not in ('上警', '不上警'):
            return

        user.skill['sheriff_vote'] = choice
        user.skill['sheriff_voted'] = True

        for bucket in ('up', 'down'):
            if user.nick in state[bucket]:
                state[bucket].remove(user.nick)

        target_bucket = 'up' if choice == '上警' else 'down'
        state[target_bucket].append(user.nick)

        pool = self._sheriff_signup_pool()
        if all(self.players[n].skill.get('sheriff_voted', False) for n in pool):
            up_list = state['up']
            msg = '上警的玩家有：' + ('、'.join(self._format_label(n) for n in up_list) if up_list else '无人')
            self.broadcast_msg(msg)

            alive_count = len(pool)
            if not up_list:
                self.broadcast_msg('无人上警，警徽流失')
                self.finish_sheriff_phase(None)
            elif len(up_list) == alive_count:
                self.broadcast_msg('全部玩家上警，警徽流失')
                self.finish_sheriff_phase(None)
            elif len(up_list) == 1:
                only_candidate = up_list[0]
                self.broadcast_msg(f'{self._format_label(only_candidate)}为唯一上警玩家，自动当选警长')
                self._declare_sheriff(only_candidate)
            else:
                self.start_sheriff_speeches()

    def start_sheriff_speeches(self):
        state = self.sheriff_state or {}
        candidates = self.get_active_sheriff_candidates()
        if not candidates:
            self.finish_sheriff_phase(None)
            return

        state['phase'] = 'speech'
        state['order_dir'] = random.choice(['asc', 'desc'])
        reverse = state['order_dir'] == 'desc'
        ordered = sorted(candidates, key=lambda nick: self.players[nick].seat or 0, reverse=reverse)
        start_idx = random.randrange(len(ordered))
        queue = ordered[start_idx:] + ordered[:start_idx]
        state['speech_queue'] = queue
        self.stage = GameStage.SPEECH
        self.current_speaker = queue[0]
        self._announce_next_speaker(is_pk=False)

    def _announce_next_speaker(self, is_pk: bool):
        state = self.sheriff_state or {}
        queue = state.get('speech_queue', [])
        if not queue:
            return
        current = queue[0]
        upcoming = queue[1] if len(queue) > 1 else None
        order_dir = state['pk_order_dir'] if is_pk else state.get('order_dir')
        order_label = '逆序' if order_dir == 'desc' else '顺序'
        if is_pk:
            prefix = '进行平票PK发言'
        else:
            prefix = '进行警长竞选发言'
        if upcoming:
            self.broadcast_msg(f"{prefix}，请{self._format_label(current)}发言，{order_label}发言顺序，{self._format_label(upcoming)}请准备。")
        else:
            self.broadcast_msg(f"{prefix}，请{self._format_label(current)}发言，{order_label}发言顺序。")

    def advance_sheriff_speech(self, finished_nick: str):
        state = self.sheriff_state or {}
        if state.get('phase') not in ('speech', 'pk_speech'):
            return
        if finished_nick != self.current_speaker:
            return
        queue = state.get('speech_queue', [])
        if queue and queue[0] == finished_nick:
            queue.pop(0)

        if queue:
            self.current_speaker = queue[0]
            self._announce_next_speaker(is_pk=state.get('phase') == 'pk_speech')
            return

        self.current_speaker = None
        self.stage = GameStage.SHERIFF
        if state.get('phase') == 'speech':
            state['phase'] = 'await_vote'
            self.broadcast_msg('所有上警玩家发言完毕，等待房主发起投票')
        else:
            state['phase'] = 'await_pk_vote'
            self.broadcast_msg('PK 玩家发言完毕，等待房主发起PK投票')

    def handle_sheriff_withdraw(self, user: User) -> Optional[str]:
        state = self.sheriff_state or {}
        if state.get('phase') not in ('speech', 'await_vote', 'pk_speech', 'await_pk_vote'):
            return '当前不可退水'
        candidates = self.get_active_sheriff_candidates()
        if user.nick not in candidates:
            return '你当前不在竞选名单'
        if user.nick in state.get('withdrawn', []):
            return '你已经退水'
        state['withdrawn'].append(user.nick)
        user.skill['sheriff_withdrawn'] = True
        self.broadcast_msg(f"{self._format_label(user.nick)}退水")
        queue = state.get('speech_queue', [])
        if user.nick in queue:
            queue[:] = [n for n in queue if n != user.nick]
            if self.current_speaker == user.nick:
                self.advance_sheriff_speech(user.nick)
        self._check_auto_elect()
        return None

    # -------------------- 狼人自曝逻辑 --------------------
    def handle_wolf_self_bomb(self, user: User) -> Optional[str]:
        if not self.can_wolf_self_bomb(user):
            return '当前不可自曝'

        bonus_target = None
        if user.role == Role.WHITE_WOLF_KING:
            validation = self._prepare_white_wolf_bomb(user)
            if isinstance(validation, str):
                return validation
            bonus_target = validation

        if self.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
            if user.role == Role.WHITE_WOLF_KING:
                self._execute_white_wolf_bomb(user, bonus_target)
            self._handle_exile_stage_bomb(user)
            return None

        state = self.sheriff_state or {}
        if self.stage == GameStage.SPEECH:
            self._handle_sheriff_stage_bomb(user, deferred=False)
            if user.role == Role.WHITE_WOLF_KING:
                self._execute_white_wolf_bomb(user, bonus_target)
            return None
        if self.stage == GameStage.SHERIFF and state.get('phase') == 'deferred_withdraw':
            self._handle_sheriff_stage_bomb(user, deferred=True)
            if user.role == Role.WHITE_WOLF_KING:
                self._execute_white_wolf_bomb(user, bonus_target)
            return None
        return '当前不可自曝'

    def _handle_exile_stage_bomb(self, user: User):
        day_state = self.day_state or {}
        phase = day_state.get('phase')
        if phase not in ('exile_speech', 'exile_pk_speech'):
            return

        self.broadcast_msg(f"{self._format_label(user.nick)}选择自曝，今日放逐发言结束。")
        queue = day_state.get('exile_speech_queue', [])
        queue.clear()
        day_state['phase'] = 'bomb_execution'
        self.current_speaker = None
        is_white_king = user.role == Role.WHITE_WOLF_KING
        include_bomber = not is_white_king
        allow_followup_speech = False
        after_stage = 'end_day'
        self._start_bomb_last_words(
            user.nick,
            after_stage=after_stage,
            include_bomber=include_bomber,
            allow_followup_speech=allow_followup_speech,
            suppress_skill_prompt=not is_white_king,
            use_badge_followup=not is_white_king
        )

    def _handle_sheriff_stage_bomb(self, user: User, deferred: bool):
        state = self.sheriff_state or {}
        self.broadcast_msg(f"{self._format_label(user.nick)}在警长竞选阶段自曝，竞选被迫中止。")
        # 清理候选与队列
        for bucket in ('up', 'withdrawn', 'pk_candidates'):
            if bucket in state:
                state[bucket] = [n for n in state[bucket] if n != user.nick]
        queue = state.get('speech_queue', [])
        if queue:
            state['speech_queue'] = [n for n in queue if n != user.nick]
        if self.current_speaker == user.nick:
            self.current_speaker = None

        self._queue_pending_day_bomb(user.nick, origin='sheriff')
        user.status = PlayerStatus.PENDING_DEAD

        bomb_count = self.skill.get('sheriff_bomb_count', 0) + 1
        self.skill['sheriff_bomb_count'] = bomb_count
        deferred_active = self.skill.get('sheriff_deferred_active', False)
        rule = self.sheriff_bomb_rule

        badge_lost = False
        if rule == SheriffBombRule.SINGLE_LOSS:
            badge_lost = True
        elif rule == SheriffBombRule.DOUBLE_LOSS:
            badge_lost = deferred_active or deferred or bomb_count >= 2

        if badge_lost:
            self.sheriff_badge_destroyed = True
            self.skill['sheriff_deferred_active'] = False
            self.skill['sheriff_deferred_payload'] = None
            self.skill['sheriff_bomb_count'] = 0
            self.broadcast_msg('警徽流失，本局将没有警长。')
        else:
            payload = {
                'up': [n for n in state.get('up', []) if n != user.nick],
                'down': state.get('down', [])[:],
                'withdrawn': [n for n in state.get('withdrawn', []) if n != user.nick],
            }
            self.skill['sheriff_deferred_payload'] = payload
            self.skill['sheriff_deferred_active'] = True
            self.broadcast_msg('警长竞选推迟至下一天，保留首日上警名单。')

        self._cancel_deferred_withdraw_timer()
        state['phase'] = 'done'
        self.finish_sheriff_phase(None)

    def _prepare_white_wolf_bomb(self, user: User):
        others = [
            u for u in self.list_alive_players()
            if u.role in WOLF_TEAM_ROLES and u.nick != user.nick
        ]
        if not others:
            return '白狼王已是最后一名狼人，无法自曝。'
        target_nick = user.skill.get('white_wolf_bomb_target')
        if not target_nick:
            return '白狼王需要先选择要击杀的玩家。'
        target = self.players.get(target_nick)
        if not target or target.status != PlayerStatus.ALIVE or target.nick == user.nick:
            return '击杀目标无效，请重新选择。'
        return target

    def _execute_white_wolf_bomb(self, user: User, target: Optional[User]):
        if not target:
            return
        if target.status != PlayerStatus.ALIVE:
            user.send_msg('目标已不在场，额外击杀失效。')
            user.skill.pop('white_wolf_bomb_target', None)
            return
        self.broadcast_msg(f"{self._format_label(user.nick)}自曝，强制带走{self._format_label(target.nick)}。")
        self._enqueue_white_wolf_kill(target)
        user.skill.pop('white_wolf_bomb_target', None)

    def _queue_pending_day_bomb(self, nick: str, origin: str):
        queue = self.skill.setdefault('pending_day_bombs', [])
        queue.append({'nick': nick, 'origin': origin})

    def _trigger_pending_day_bomb_flow(self) -> bool:
        pending = self.skill.get('pending_day_bombs')
        if not pending:
            if pending == []:
                self.skill.pop('pending_day_bombs', None)
            return False
        while pending:
            entry = pending.pop(0)
            bomber = self.players.get(entry.get('nick')) if entry else None
            if not bomber:
                continue
            is_white_king = bomber.role == Role.WHITE_WOLF_KING
            if not is_white_king:
                self.broadcast_msg('由于狼人自曝，今日直接进入遗言阶段。')
            self.day_state['phase'] = 'last_words'
            include_bomber = not is_white_king
            allow_follow = False
            suppress_prompt = not is_white_king
            self._start_bomb_last_words(
                bomber.nick,
                after_stage='announcement',
                include_bomber=include_bomber,
                allow_followup_speech=allow_follow,
                suppress_skill_prompt=suppress_prompt,
                use_badge_followup=not is_white_king
            )
            if not pending:
                self.skill.pop('pending_day_bombs', None)
            return True
        self.skill.pop('pending_day_bombs', None)
        return False

    def _enqueue_white_wolf_kill(self, target: Optional[User]):
        if not target or target.status == PlayerStatus.DEAD:
            return
        if self.stage == GameStage.LAST_WORDS and (self.day_state or {}).get('phase') == 'last_words':
            self.handle_last_word_skill_kill(target.nick, from_day_execution=True)
            return
        target.status = PlayerStatus.PENDING_DEAD
        day_deaths = self.day_state.setdefault('day_deaths', [])
        if target.nick not in day_deaths:
            day_deaths.append(target.nick)
        pending = self.skill.setdefault('white_wolf_pending_kills', [])
        if target.nick not in pending:
            pending.append(target.nick)

    def _start_bomb_last_words(
        self,
        nick: str,
        after_stage: str = 'end_day',
        include_bomber: bool = True,
        allow_followup_speech: bool = False,
        suppress_skill_prompt: bool = True,
        use_badge_followup: bool = True
    ):
        player = self.players.get(nick)
        if not player:
            return
        player.status = PlayerStatus.PENDING_DEAD
        self.day_state['pending_execution'] = nick
        day_deaths = self.day_state.setdefault('day_deaths', [])
        if nick not in day_deaths:
            day_deaths.append(nick)
        queue = []
        if include_bomber:
            queue.append(nick)
        extras = self.skill.pop('white_wolf_pending_kills', [])
        for extra in extras:
            if extra in self.players and extra not in queue:
                queue.append(extra)
                if extra not in day_deaths:
                    day_deaths.append(extra)
        if not queue:
            self._resolve_post_death_after_stage(after_stage)
            return
        if use_badge_followup:
            self._set_badge_followup(
                queue=queue,
                allow_speech=allow_followup_speech,
                after_stage=after_stage,
                randomize=False,
                skip_first_skill_msg=suppress_skill_prompt,
                disable_skill_prompt=suppress_skill_prompt
            )
        self.start_last_words(
            queue,
            allow_speech=allow_followup_speech,
            after_stage='badge_transfer' if use_badge_followup else after_stage,
            disable_skill_prompt=suppress_skill_prompt
        )

    def _check_auto_elect(self):
        state = self.sheriff_state or {}
        if state.get('phase') not in ('speech', 'await_vote'):
            return
        candidates = self.get_active_sheriff_candidates()
        if len(candidates) == 1:
            self._declare_sheriff(candidates[0])

    def resume_deferred_sheriff_phase(self):
        payload = self.skill.get('sheriff_deferred_payload') or {}
        up = [nick for nick in payload.get('up', []) if self._is_alive(nick)]
        down = [nick for nick in payload.get('down', []) if self._is_alive(nick)]
        withdrawn = [nick for nick in payload.get('withdrawn', []) if nick in up]

        if not up:
            self.sheriff_badge_destroyed = True
            self.skill['sheriff_deferred_active'] = False
            self.skill['sheriff_deferred_payload'] = None
            self.broadcast_msg('无存活的上警玩家，警徽流失。')
            state = {'phase': 'done'}
            self.sheriff_state = state
            self.finish_sheriff_phase(None)
            return

        state = {
            'phase': 'deferred_withdraw',
            'up': up,
            'down': down,
            'withdrawn': withdrawn,
            'order_dir': None,
            'speech_queue': [],
            'pk_order_dir': None,
            'pk_candidates': [],
            'eligible_voters': [],
            'vote_records': {},
        }
        self.sheriff_state = state
        self.current_speaker = None
        remain = '、'.join(self._format_label(nick) for nick in up)
        self.broadcast_msg(f'上一日狼人自曝，保留上警玩家：{remain}。10 秒内可退水。')
        self._start_deferred_withdraw_timer()

    def _start_deferred_withdraw_timer(self, seconds: int = 10):
        self._cancel_deferred_withdraw_timer()
        task = asyncio.create_task(self._deferred_withdraw_timer(seconds))
        self.skill['deferred_withdraw_task'] = task

    def _cancel_deferred_withdraw_timer(self):
        task = self.skill.pop('deferred_withdraw_task', None)
        if task:
            task.cancel()

    async def _deferred_withdraw_timer(self, seconds: int):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        if self.sheriff_state.get('phase') == 'deferred_withdraw':
            self.complete_deferred_withdraw()

    def complete_deferred_withdraw(self):
        state = self.sheriff_state or {}
        if state.get('phase') != 'deferred_withdraw':
            return
        self._cancel_deferred_withdraw_timer()

        candidates = self.get_active_sheriff_candidates()
        if not candidates:
            self.sheriff_badge_destroyed = True
            self.skill['sheriff_deferred_active'] = False
            self.skill['sheriff_deferred_payload'] = None
            self.skill['sheriff_bomb_count'] = 0
            self.broadcast_msg('无人上警，警徽流失。')
            state['phase'] = 'done'
            self.finish_sheriff_phase(None)
            return

        if len(candidates) == 1:
            self.skill['sheriff_deferred_active'] = False
            self.skill['sheriff_deferred_payload'] = None
            self.skill['sheriff_bomb_count'] = 0
            self._declare_sheriff(candidates[0])
            return

        state['phase'] = 'await_vote'
        names = '、'.join(self._format_label(nick) for nick in candidates)
        self.broadcast_msg(f'仍在警上的玩家有：{names}，请没有上警的玩家投票。')
        self.start_sheriff_vote(pk_mode=False)

    def start_sheriff_vote(self, pk_mode: bool = False) -> Optional[str]:
        state = self.sheriff_state or {}
        expected_phase = 'await_pk_vote' if pk_mode else 'await_vote'
        target_phase = 'pk_vote' if pk_mode else 'vote'
        if state.get('phase') != expected_phase:
            return '当前无法发起投票'

        candidates = self.get_active_sheriff_candidates()
        if not candidates:
            self.broadcast_msg('无人可供投票，警长竞选结束')
            self.finish_sheriff_phase(None)
            return None

        if pk_mode:
            state['pk_candidates'] = candidates
            eligible = [
                u.nick for u in self.list_alive_players()
                if u.nick not in candidates and self._can_player_vote(u.nick)
            ]
            prompt = '请非PK玩家在10秒内完成PK投票'
        else:
            eligible = [
                nick for nick in state.get('down', [])
                if self._is_alive(nick) and self._can_player_vote(nick)
            ]
            prompt = '不上警玩家请在10秒内完成投票'

        state['eligible_voters'] = eligible
        state['vote_records'] = {}
        state['phase'] = target_phase

        for voter in eligible:
            player = self.players.get(voter)
            if player:
                player.skill['sheriff_has_balloted'] = False
                player.skill['sheriff_vote_pending'] = True

        self.broadcast_msg(prompt)
        if not eligible:
            self.finish_sheriff_vote()
            return None

        self._start_sheriff_vote_timer()
        return None

    def record_sheriff_ballot(self, user: User, target: str):
        state = self.sheriff_state or {}
        if state.get('phase') not in ('vote', 'pk_vote'):
            return
        if user.nick not in state.get('eligible_voters', []):
            return
        if not self._can_player_vote(user.nick):
            return
        if user.skill.get('sheriff_has_balloted'):
            return

        valid_targets = self.get_active_sheriff_candidates()
        if target not in valid_targets and target != '弃票':
            user.send_msg('无效投票目标')
            return

        user.skill['sheriff_has_balloted'] = True
        user.skill['sheriff_vote_pending'] = False
        user.skill['sheriff_ballot_choice'] = target
        state.setdefault('vote_records', {}).setdefault(target, []).append(user.nick)

        eligible = [nick for nick in state.get('eligible_voters', []) if self._is_alive(nick)]
        if all(self.players[n].skill.get('sheriff_has_balloted') for n in eligible):
            self.finish_sheriff_vote()

    def finish_sheriff_vote(self):
        state = self.sheriff_state or {}
        if state.get('phase') not in ('vote', 'pk_vote'):
            return
        self._cancel_sheriff_vote_timer()

        candidates = self.get_active_sheriff_candidates()
        vote_records = state.get('vote_records', {})
        for nick in candidates:
            voters = vote_records.get(nick, [])
            seats = '、'.join(self._format_label(v) for v in voters) if voters else '无'
            self.broadcast_msg(f"{self._format_label(nick)}得票：{seats}")
        abstain = vote_records.get('弃票', [])
        if abstain:
            seats = '、'.join(self._format_label(v) for v in abstain)
            self.broadcast_msg(f"弃票：{seats}")

        # 统计结果
        tally = {nick: len(vote_records.get(nick, [])) for nick in candidates}
        if not tally:
            self.broadcast_msg('无人投票，警长竞选流拍')
            self.finish_sheriff_phase(None)
            return

        max_votes = max(tally.values())
        winners = [nick for nick, count in tally.items() if count == max_votes]

        phase = state.get('phase')
        if phase == 'vote':
            if len(winners) == 1:
                self._declare_sheriff(winners[0])
            else:
                state['phase'] = 'pk_setup'
                state['pk_candidates'] = winners
                self.start_pk_speech()
        else:
            if len(winners) == 1:
                self._declare_sheriff(winners[0])
            else:
                self.broadcast_msg('PK 投票仍然平票，无人当选警长，警徽流失')
                self.finish_sheriff_phase(None)

    def _start_sheriff_vote_timer(self, seconds: int = 10):
        self._cancel_sheriff_vote_timer()
        task = asyncio.create_task(self._sheriff_vote_timeout(seconds))
        self.skill['sheriff_vote_task'] = task

    def _cancel_sheriff_vote_timer(self):
        task = self.skill.pop('sheriff_vote_task', None)
        if task:
            task.cancel()

    async def _sheriff_vote_timeout(self, seconds: int):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        state = self.sheriff_state or {}
        if state.get('phase') not in ('vote', 'pk_vote'):
            return
        eligible = state.get('eligible_voters', [])
        for nick in eligible:
            player = self.players.get(nick)
            if not player or not self._is_alive(nick):
                continue
            if player.skill.get('sheriff_has_balloted'):
                continue
            player.skill['sheriff_has_balloted'] = True
            player.skill['sheriff_vote_pending'] = False
            vote_records = state.setdefault('vote_records', {})
            abstain = vote_records.setdefault('弃票', [])
            if nick not in abstain:
                abstain.append(nick)
        self.finish_sheriff_vote()

    def start_pk_speech(self):
        state = self.sheriff_state or {}
        candidates = [nick for nick in state.get('pk_candidates', []) if self._is_sheriff_eligible(nick)]
        if not candidates:
            self.finish_sheriff_phase(None)
            return
        base_dir = state.get('order_dir') or 'asc'
        state['pk_order_dir'] = 'desc' if base_dir == 'asc' else 'asc'
        reverse = state['pk_order_dir'] == 'desc'
        ordered = sorted(candidates, key=lambda nick: self.players[nick].seat or 0, reverse=reverse)
        state['speech_queue'] = ordered
        state['phase'] = 'pk_speech'
        self.stage = GameStage.SPEECH
        self.current_speaker = ordered[0]
        names = '、'.join(self._format_label(nick) for nick in ordered)
        self.broadcast_msg(f"{names} 进入平票PK。")
        self._announce_next_speaker(is_pk=True)

    def _declare_sheriff(self, nick: str):
        player = self.players.get(nick)
        if not player:
            self.finish_sheriff_phase(None)
            return
        self.broadcast_msg(f"{self._format_label(nick)}当选警长，请房主公布昨夜信息。", tts=True)
        self.skill['sheriff_captain'] = nick
        self.skill['sheriff_deferred_active'] = False
        self.skill['sheriff_deferred_payload'] = None
        self.skill['sheriff_bomb_count'] = 0
        self.finish_sheriff_phase(nick)

    def finish_sheriff_phase(self, winner: Optional[str]):
        state = self.sheriff_state or {}
        state['phase'] = 'done'
        self.current_speaker = None
        if self.stage != GameStage.Day:
            self.stage = GameStage.Day
        for user in self.players.values():
            user.skill['sheriff_vote_pending'] = False
        if not self.day_state:
            announce = winner is None
            self.prepare_day_phase(announce=announce)

    # -------------------- 白天阶段逻辑 --------------------
    def prepare_day_phase(self, announce: bool = True):
        self.stage = GameStage.Day
        self.day_state = {
            'phase': 'announcement',
            'last_words_queue': list(self.death_pending),
            'current_last_word': None,
            'last_words_allow_speech': True,
            'after_last_words': 'exile_speech',
            'exile_speech_queue': [],
            'vote_candidates': [],
            'eligible_voters': [],
            'vote_records': {},
            'pk_candidates': [],
            'pending_execution': None,
            'night_deaths': [],
            'day_deaths': [],
            'night_anchor': None,
            'sheriff_order_pending': False,
            'pending_badge_followup': None,
            'pending_announcement_broadcast': False,
        }
        has_pending_day_bombs = bool(self.skill.get('pending_day_bombs'))
        if has_pending_day_bombs and announce:
            self.day_state['pending_announcement_broadcast'] = True
        else:
            self.day_state['pending_announcement_broadcast'] = False
            if announce:
                self.broadcast_msg('请房主公布昨夜信息')
        if has_pending_day_bombs:
            self._trigger_pending_day_bomb_flow()

    async def publish_night_info(self) -> Optional[str]:
        state = self.day_state or {}
        if state.get('phase') != 'announcement':
            return '当前不需要公布'

        death_list = [nick for nick in self.death_pending if nick in self.players]
        formatted = [self._format_label(nick) for nick in death_list]
        self.day_state['night_deaths'] = death_list[:]
        self.day_state['night_anchor'] = death_list[0] if len(death_list) == 1 else None

        if not death_list:
            self.broadcast_msg('昨夜平安夜，无人出局。')
            self.prompt_sheriff_order()
        else:
            summary = '和'.join(formatted) if len(formatted) > 1 else formatted[0]
            if len(formatted) == 1:
                self.broadcast_msg(f'昨夜{summary}死亡，等待玩家发动技能')
            else:
                self.broadcast_msg(f'昨夜{summary}死亡')
            # 夜晚遗言顺序随机
            speech_order = death_list[:]
            random.shuffle(speech_order)
            allow_speech = (self.round == 1)
            skip_msg = len(formatted) == 1
            self._set_badge_followup(
                queue=speech_order,
                allow_speech=allow_speech,
                after_stage='exile_speech',
                randomize=False,
                skip_first_skill_msg=skip_msg,
                disable_skill_prompt=False
            )
            self.start_last_words(
                speech_order,
                allow_speech=False,
                after_stage='badge_transfer',
                randomize=False,
                skip_first_skill_msg=False
            )

        self.death_pending = []

    def start_last_words(
        self,
        queue: List[str],
        allow_speech: bool,
        after_stage: str,
        randomize: bool = False,
        skip_first_skill_msg: bool = False,
        disable_skill_prompt: bool = False
    ):
        valid_queue = self._sanitize_last_words_queue(queue, randomize)
        if not valid_queue:
            if after_stage == 'exile_speech':
                self.start_exile_speech()
            elif after_stage == 'end_day':
                self.end_day_phase()
            return
        self.day_state['phase'] = 'last_words'
        self.day_state['last_words_queue'] = valid_queue
        self.day_state['current_last_word'] = valid_queue[0]
        self.day_state['last_words_allow_speech'] = allow_speech
        self.day_state['after_last_words'] = after_stage
        self.day_state['last_words_skip_skill_prompt'] = disable_skill_prompt
        self.stage = GameStage.LAST_WORDS
        for nick in valid_queue:
            self._prepare_last_words_player(self.players.get(nick), disable_skill_prompt)
        if disable_skill_prompt:
            self.day_state['last_word_skill_announced'] = True
        else:
            self.day_state['last_word_skill_announced'] = skip_first_skill_msg
        self.day_state['last_word_speech_announced'] = False
        self._kickoff_last_word_prompt()

    def handle_last_word_skill_choice(self, user: User, choice: str):
        if self.day_state.get('phase') != 'last_words':
            return
        if user.nick != self.day_state.get('current_last_word'):
            return
        if choice == '发动技能':
            if user.role in (Role.HUNTER, Role.WOLF_KING) and not user.skill.get('can_shoot', True):
                user.send_msg('你无法发动技能，请选择放弃。')
                return
            user.skill['pending_last_skill'] = True
        else:
            user.skill['pending_last_skill'] = False
            user.skill['last_words_skill_resolved'] = True
            self._advance_last_words_if_ready(user)

    def complete_last_word_speech(self, user: User):
        if self.day_state.get('phase') != 'last_words':
            return
        if user.nick != self.day_state.get('current_last_word'):
            return
        user.skill['last_words_done'] = True
        self._advance_last_words_if_ready(user)

    def _advance_last_words_if_ready(self, user: User):
        allow_speech = self.day_state.get('last_words_allow_speech', True)
        if not user.skill.get('last_words_skill_resolved', False):
            return
        if allow_speech and not user.skill.get('last_words_done', False):
            self._prompt_current_last_word_speech()
            return
        queue = self.day_state.get('last_words_queue', [])
        if queue and queue[0] == user.nick:
            queue.pop(0)
        if queue:
            self.day_state['current_last_word'] = queue[0]
            self.day_state['last_word_speech_announced'] = False
            skip_skill_prompt = self.day_state.get('last_words_skip_skill_prompt', False)
            self._prepare_last_words_player(self.players.get(queue[0]), skip_skill_prompt)
            self.day_state['last_word_skill_announced'] = skip_skill_prompt
            self._kickoff_last_word_prompt()
        else:
            self._schedule_victory_check()
            next_stage = self.day_state.get('after_last_words')
            if next_stage == 'exile_speech':
                self.prompt_sheriff_order()
            elif next_stage == 'badge_transfer':
                self._mark_followup_skills_resolved()
                self._start_badge_transfer_phase()
            elif next_stage == 'end_day':
                self._finalize_day_execution()

    def advance_last_words_progress(self, user: User):
        self._advance_last_words_if_ready(user)

    def _announce_last_word_skill(self):
        current = self.day_state.get('current_last_word')
        if not current:
            return
        if not self.day_state.get('last_word_skill_announced', False):
            self.broadcast_msg(f'等待{self._format_label(current)}发动技能')
            self.day_state['last_word_skill_announced'] = True
            self.day_state['last_word_speech_announced'] = False

    def _prompt_current_last_word_speech(self):
        current = self.day_state.get('current_last_word')
        if not current:
            return
        if not self.day_state.get('last_word_speech_announced', False):
            self.broadcast_msg(f'请{self._format_label(current)}发表遗言')
            self.day_state['last_word_speech_announced'] = True
            self.day_state['last_word_skill_announced'] = True

    def _sanitize_last_words_queue(self, queue: List[str], randomize: bool) -> List[str]:
        valid_queue = [nick for nick in queue if nick in self.players]
        if randomize and len(valid_queue) > 1:
            random.shuffle(valid_queue)
        return valid_queue

    def _prepare_last_words_player(self, player: Optional[User], disable_skill_prompt: bool):
        if not player:
            return
        supports_skill = self._player_supports_last_skill(player)
        if disable_skill_prompt or not supports_skill:
            player.skill['last_words_skill_resolved'] = True
        else:
            player.skill['last_words_skill_resolved'] = False
        player.skill['last_words_done'] = False
        player.skill['pending_last_skill'] = False

    def _player_supports_last_skill(self, player: Optional[User]) -> bool:
        if not player or not player.role_instance:
            return False
        supports = getattr(player.role_instance, 'supports_last_skill', None)
        if supports is None:
            return False
        return bool(supports())

    def _mark_followup_skills_resolved(self):
        followup = self.day_state.get('pending_badge_followup')
        if not followup:
            return
        followup['disable_skill_prompt'] = True
        followup['skip_first_skill_msg'] = True

    def _kickoff_last_word_prompt(self):
        if self.day_state.get('phase') != 'last_words':
            return
        current = self.day_state.get('current_last_word')
        if not current:
            return
        player = self.players.get(current)
        if not player:
            return
        allow_speech = self.day_state.get('last_words_allow_speech', True)
        skip_skill_prompt = self.day_state.get('last_words_skip_skill_prompt', False)

        if skip_skill_prompt:
            self.day_state['last_word_skill_announced'] = True
            if allow_speech:
                if not self.day_state.get('last_word_speech_announced', False):
                    self._prompt_current_last_word_speech()
            else:
                player.skill['last_words_done'] = True
                self._advance_last_words_if_ready(player)
            return

        if not self._player_supports_last_skill(player):
            player.skill['last_words_skill_resolved'] = True
            player.skill['pending_last_skill'] = False

        if player.skill.get('last_words_skill_resolved', False):
            self.day_state['last_word_skill_announced'] = True
            if allow_speech:
                if not player.skill.get('last_words_done', False):
                    self._prompt_current_last_word_speech()
            else:
                player.skill['last_words_done'] = True
                self._advance_last_words_if_ready(player)
            return

        if not self.day_state.get('last_word_skill_announced', False):
            self._announce_last_word_skill()

    def _schedule_victory_check(self):
        if self.game_over:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        loop.create_task(self.check_game_end())

    def prompt_sheriff_order(self):
        if self._trigger_pending_day_bomb_flow():
            return

        captain = self.skill.get('sheriff_captain')
        captain_alive = captain and self._is_alive(captain)
        self.broadcast_msg('放逐发言阶段，请警长选择发言顺序')

        if captain_alive:
            self.stage = GameStage.SHERIFF
            self.day_state['phase'] = 'await_sheriff_order'
            self.day_state['sheriff_order_pending'] = True
            return

        queue, start_player, direction = self._random_queue_without_sheriff()
        if queue:
            label = '顺序发言' if direction == 'asc' else '逆序发言'
            start_label = self._format_label(start_player.nick) if start_player else '首位玩家'
            self.day_state['sheriff_order_pending'] = False
            self.broadcast_msg(f'当前没有警长，系统随机选择{label}，{start_label}请发言')
            self.start_exile_speech(queue=queue, announce=False)
        else:
            self.broadcast_msg('当前无可发言玩家，直接进入放逐投票')
            self.day_state['phase'] = 'await_exile_vote'
            self.stage = GameStage.Day

    def set_sheriff_order(self, user: User, choice: str, auto: bool = False) -> Optional[str]:
        if self.day_state.get('phase') != 'await_sheriff_order':
            return '当前无需设置发言顺序'
        captain = self.skill.get('sheriff_captain')
        if user.nick != captain or not self._is_alive(user.nick):
            return '只有在场警长可以操作'
        mapping = {
            '顺序发言': 'asc',
            '逆序发言': 'desc',
        }
        direction = mapping.get(choice)
        if not direction:
            return '无效发言顺序'

        queue = self._build_directional_queue(direction)
        self.day_state['sheriff_order_pending'] = False
        if queue:
            self.start_exile_speech(queue=queue, announce=False)
            first = queue[0]
            prefix = '警长未在规定时间内选择，系统自动设为' if auto else '警长选择'
            self.broadcast_msg(f'{prefix}{choice}，{self._format_label(first)}请发言')
        else:
            self.broadcast_msg('无人可发言，直接进入放逐投票')
            self.day_state['phase'] = 'await_exile_vote'
            self.stage = GameStage.Day
        return None

    def _build_directional_queue(self, direction: str) -> List[str]:
        alive = self.list_alive_players()
        if not alive:
            return []

        captain_nick = self.skill.get('sheriff_captain')
        captain = next((u for u in alive if u.nick == captain_nick), None)
        others = [u for u in alive if not captain or u.nick != captain.nick]
        if not others:
            return [captain.nick] if captain else []

        ordered = sorted(others, key=lambda u: u.seat or 0)
        anchor_seat = None
        night_deaths = self.day_state.get('night_deaths', [])
        if len(night_deaths) == 1:
            anchor = self.players.get(night_deaths[0])
            anchor_seat = anchor.seat if anchor else None

        if direction == 'asc':
            step = 1
        else:
            step = -1

        if anchor_seat is not None:
            start_idx = self._anchor_start_index(ordered, anchor_seat, direction)
        elif captain:
            start_idx = self._captain_start_index(ordered, captain.seat or 0, direction)
        else:
            start_idx = 0 if direction == 'asc' else len(ordered) - 1

        rotated = self._rotate_players(ordered, start_idx, step)
        queue = [p.nick for p in rotated]
        if captain:
            queue.append(captain.nick)
        return queue

    def _anchor_start_index(self, ordered: List[User], anchor_seat: int, direction: str) -> int:
        if direction == 'asc':
            for idx, player in enumerate(ordered):
                if (player.seat or 0) > anchor_seat:
                    return idx
            return 0
        else:
            for idx in range(len(ordered) - 1, -1, -1):
                if (ordered[idx].seat or 0) < anchor_seat:
                    return idx
            return len(ordered) - 1

    def _captain_start_index(self, ordered: List[User], captain_seat: int, direction: str) -> int:
        if direction == 'asc':
            for idx, player in enumerate(ordered):
                if (player.seat or 0) > captain_seat:
                    return idx
            return 0
        else:
            for idx in range(len(ordered) - 1, -1, -1):
                if (ordered[idx].seat or 0) < captain_seat:
                    return idx
            return len(ordered) - 1

    def _rotate_players(self, players: List[User], start_idx: int, step: int) -> List[User]:
        if not players:
            return []
        n = len(players)
        idx = start_idx % n
        ordered = []
        for _ in range(n):
            ordered.append(players[idx])
            idx = (idx + step) % n
        return ordered

    def _build_queue_from_player(self, start_nick: str, direction: str) -> List[str]:
        alive = sorted(self.list_alive_players(), key=lambda u: u.seat or 0)
        if not alive:
            return []
        ordered = alive
        try:
            start_idx = next(i for i, u in enumerate(ordered) if u.nick == start_nick)
        except StopIteration:
            start_idx = 0
        step = 1 if direction == 'asc' else -1
        rotated = self._rotate_players(ordered, start_idx, step)
        return [p.nick for p in rotated]

    def _random_queue_without_sheriff(self) -> Tuple[List[str], Optional[User], Optional[str]]:
        alive = self.list_alive_players()
        if not alive:
            return [], None, None
        start_player = random.choice(alive)
        direction = random.choice(['asc', 'desc'])
        queue = self._build_queue_from_player(start_player.nick, direction)
        return queue, start_player, direction

    def force_sheriff_order_random(self):
        if self.day_state.get('phase') != 'await_sheriff_order':
            return
        captain_nick = self.skill.get('sheriff_captain')
        captain = self.players.get(captain_nick) if captain_nick else None
        if not captain or not self._is_alive(captain_nick):
            self.day_state['sheriff_order_pending'] = False
            self.prompt_sheriff_order()
            return
        choice = random.choice(['顺序发言', '逆序发言'])
        self.broadcast_msg(f'{self._format_label(captain_nick)}超时未选择发言顺序，系统自动{choice}')
        self.set_sheriff_order(captain, choice, auto=True)

    def handle_sheriff_badge_action(self, user: User, choice: str) -> Optional[str]:
        current = self.skill.get('sheriff_captain')
        if current != user.nick:
            return '只有现任警长可以操作'
        if user.skill.get('badge_action_taken', False):
            return '你已经做出选择'

        if choice and choice.startswith('transfer:'):
            target_nick = choice.split(':', 1)[1]
            target = self.players.get(target_nick)
            if not target or target.status != PlayerStatus.ALIVE:
                return '目标玩家无效'
            self.skill['sheriff_captain'] = target_nick
            user.skill['badge_action_taken'] = True
            self.broadcast_msg(f'{self._format_label(user.nick)}移交警徽给{self._format_label(target_nick)}。')
            self._complete_badge_transfer_phase()
            return None

        if choice == 'destroy':
            self.skill['sheriff_captain'] = None
            self.sheriff_badge_destroyed = True
            self.skill['sheriff_deferred_active'] = False
            self.skill['sheriff_deferred_payload'] = None
            self.skill['sheriff_bomb_count'] = 0
            user.skill['badge_action_taken'] = True
            self.broadcast_msg('警徽被撕毁，本局将没有警长。')
            self._complete_badge_transfer_phase()
            return None

        return '无效的警徽操作'

    def handle_idiot_badge_transfer(self, user: User, target_nick: Optional[str]) -> Optional[str]:
        if not user.skill.get('idiot_badge_transfer_required'):
            return '当前无需移交警徽'
        if self.skill.get('sheriff_captain') != user.nick:
            user.skill['idiot_badge_transfer_required'] = False
            return '你已不再持有警徽'
        if target_nick == 'forfeit' or not target_nick:
            self.skill['sheriff_captain'] = None
            self.broadcast_msg('警徽无人接任，暂时空缺。')
            user.skill['idiot_badge_transfer_required'] = False
            return None
        target = self.players.get(target_nick)
        if not target or target.status != PlayerStatus.ALIVE or target.nick == user.nick:
            return '目标玩家无效'
        self.skill['sheriff_captain'] = target.nick
        user.skill['idiot_badge_transfer_required'] = False
        self.broadcast_msg(f'{self._format_label(user.nick)}将警徽移交给{self._format_label(target.nick)}。')
        return None

    def start_exile_speech(self, queue: Optional[List[str]] = None, announce: bool = True):
        if queue is None:
            alive = sorted(self.list_alive_players(), key=lambda u: u.seat or 0)
            queue = [u.nick for u in alive]
        if not queue:
            self.end_day_phase()
            return
        self.day_state['phase'] = 'exile_speech'
        self.day_state['exile_speech_queue'] = queue
        self.stage = GameStage.EXILE_SPEECH
        self.current_speaker = queue[0]
        if announce:
            self._announce_exile_speaker(False)

    def _announce_exile_speaker(self, pk: bool):
        queue = self.day_state.get('exile_speech_queue', [])
        if not queue:
            return
        current = queue[0]
        upcoming = queue[1] if len(queue) > 1 else None
        prefix = '放逐PK发言' if pk else '放逐发言阶段'
        if upcoming:
            self.broadcast_msg(f"{prefix}，请{self._format_label(current)}发言，{self._format_label(upcoming)}请准备。")
        else:
            self.broadcast_msg(f"{prefix}，请{self._format_label(current)}发言。")

    def advance_exile_speech(self):
        if self.day_state.get('phase') not in ('exile_speech', 'exile_pk_speech'):
            return
        queue = self.day_state.get('exile_speech_queue', [])
        if queue:
            queue.pop(0)
        if queue:
            self.current_speaker = queue[0]
            self._announce_exile_speaker(self.day_state.get('phase') == 'exile_pk_speech')
        else:
            self.current_speaker = None
            if self.day_state.get('phase') == 'exile_speech':
                self.day_state['phase'] = 'await_exile_vote'
                self.stage = GameStage.Day
                self.broadcast_msg('放逐发言结束，等待房主发起放逐投票')
            else:
                self.day_state['phase'] = 'await_exile_pk_vote'
                self.stage = GameStage.Day
                self.broadcast_msg('PK 发言结束，等待房主发起放逐PK投票')

    def start_exile_vote(self, pk_mode: bool = False) -> Optional[str]:
        phase = self.day_state.get('phase')
        if pk_mode and phase != 'await_exile_pk_vote':
            return '当前无法发起PK投票'
        if not pk_mode and phase != 'await_exile_vote':
            return '当前无法发起放逐投票'
        if pk_mode:
            candidates = [nick for nick in self.day_state.get('pk_candidates', []) if self._is_alive(nick)]
            eligible = [
                u.nick for u in self.list_alive_players()
                if u.nick not in candidates and self._can_player_vote(u.nick)
            ]
            stage = GameStage.EXILE_PK_VOTE
            new_phase = 'exile_pk_vote'
        else:
            candidates = [u.nick for u in self.list_alive_players()]
            eligible = [u.nick for u in self.list_alive_players() if self._can_player_vote(u.nick)]
            stage = GameStage.EXILE_VOTE
            new_phase = 'exile_vote'
        if not candidates or not eligible:
            return '当前无法完成投票'
        self.day_state['vote_candidates'] = candidates
        self.day_state['eligible_voters'] = eligible
        self.day_state['vote_records'] = {}
        self.day_state['phase'] = new_phase
        self.stage = stage
        for nick in eligible:
            player = self.players.get(nick)
            if player:
                player.skill['exile_has_balloted'] = False
                player.skill['exile_vote_pending'] = True
        self.broadcast_msg('放逐投票进行中，请在10秒内完成选择')

    def record_exile_vote(self, user: User, target: str):
        phase = self.day_state.get('phase')
        if phase not in ('exile_vote', 'exile_pk_vote'):
            return
        if user.nick not in self.day_state.get('eligible_voters', []):
            return
        if not self._can_player_vote(user.nick):
            return
        if user.skill.get('exile_has_balloted'):
            return
        valid_targets = self.day_state.get('vote_candidates', [])
        if target not in valid_targets and target != '弃票':
            user.send_msg('无效投票')
            return
        user.skill['exile_has_balloted'] = True
        user.skill['exile_vote_pending'] = False
        self.day_state.setdefault('vote_records', {}).setdefault(target, []).append(user.nick)
        eligible = [nick for nick in self.day_state.get('eligible_voters', []) if self._is_alive(nick)]
        if all(self.players[n].skill.get('exile_has_balloted') for n in eligible):
            self.finish_exile_vote()

    def finish_exile_vote(self):
        phase = self.day_state.get('phase')
        if phase not in ('exile_vote', 'exile_pk_vote'):
            return
        candidates = self.day_state.get('vote_candidates', [])
        records = self.day_state.get('vote_records', {})
        result_lines = ['放逐投票结果：']
        for nick in candidates:
            voters = records.get(nick, [])
            if not voters:
                continue
            seats = '、'.join(self._format_label(v) for v in voters)
            result_lines.append(f"{self._format_label(nick)}得票<- {seats}")
        abstain = records.get('弃票', [])
        if abstain:
            seats = '、'.join(self._format_label(v) for v in abstain)
            result_lines.append(f"弃票：{seats}")
        self.broadcast_msg('\n'.join(result_lines))

        tally = {nick: len(records.get(nick, [])) for nick in candidates}
        max_votes = max(tally.values()) if tally else 0
        if max_votes == 0:
            self.broadcast_msg('放逐失败，无人出局')
            self.end_day_phase()
            return
        winners = [nick for nick, cnt in tally.items() if cnt == max_votes]
        if len(winners) == 1:
            self.start_execution_sequence(winners[0])
        else:
            if phase == 'exile_vote':
                self.day_state['pk_candidates'] = winners
                self.start_exile_pk_speech()
            else:
                self.broadcast_msg('放逐失败，无人出局')
                self.end_day_phase()

    def start_exile_pk_speech(self):
        candidates = [nick for nick in self.day_state.get('pk_candidates', []) if self._is_alive(nick)]
        if not candidates:
            self.end_day_phase()
            return
        ordered = sorted(candidates, key=lambda nick: self.players[nick].seat or 0)
        self.day_state['phase'] = 'exile_pk_speech'
        self.day_state['exile_speech_queue'] = ordered
        self.stage = GameStage.EXILE_PK_SPEECH
        self.current_speaker = ordered[0]
        self._announce_exile_speaker(True)

    def start_execution_sequence(self, nick: str):
        target = self.players.get(nick)
        if not target:
            self.end_day_phase()
            return
        if target.role == Role.IDIOT and not target.skill.get('idiot_flipped', False):
            self._handle_idiot_flip(target)
            return
        target.status = PlayerStatus.PENDING_DEAD
        self.day_state['pending_execution'] = nick
        self.day_state['day_deaths'] = [nick]
        self.broadcast_msg(f"{self._format_label(nick)}被放逐，进入被动技能阶段")
        queue = self.day_state['day_deaths'][:]
        self._set_badge_followup(
            queue=queue,
            allow_speech=True,
            after_stage='end_day',
            randomize=False,
            skip_first_skill_msg=True,
            disable_skill_prompt=True
        )
        self.start_last_words(queue, allow_speech=False, after_stage='badge_transfer')

    def handle_last_word_skill_kill(self, nick: str, from_day_execution: bool = False):
        player = self.players.get(nick)
        if not player or player.status == PlayerStatus.DEAD:
            return
        player.status = PlayerStatus.PENDING_DEAD
        queue = self.day_state.get('last_words_queue')
        if queue is None:
            queue = []
            self.day_state['last_words_queue'] = queue
        if nick not in queue:
            queue.append(nick)
        player.skill['last_words_skill_resolved'] = False
        player.skill['last_words_done'] = False
        player.skill['pending_last_skill'] = False
        if from_day_execution:
            day_deaths = self.day_state.setdefault('day_deaths', [])
            if nick not in day_deaths:
                day_deaths.append(nick)
        followup = self.day_state.get('pending_badge_followup')
        if followup:
            queue_ref = followup.setdefault('queue', [])
            if nick not in queue_ref:
                queue_ref.append(nick)

    def _set_badge_followup(self, queue: List[str], allow_speech: bool, after_stage: str, randomize: bool = False,
                            skip_first_skill_msg: bool = False, disable_skill_prompt: bool = False):
        self.day_state['pending_badge_followup'] = {
            'queue': [nick for nick in queue if nick in self.players],
            'allow_speech': allow_speech,
            'after_stage': after_stage,
            'randomize': randomize,
            'skip_first_skill_msg': skip_first_skill_msg,
            'disable_skill_prompt': disable_skill_prompt,
        }

    def _start_badge_transfer_phase(self):
        followup = self.day_state.get('pending_badge_followup')
        if not followup:
            self._launch_badge_followup()
            return
        captain = self.skill.get('sheriff_captain')
        if not captain or captain not in followup.get('queue', []):
            self._launch_badge_followup()
            return
        player = self.players.get(captain)
        if not player:
            self.skill['sheriff_captain'] = None
            self._launch_badge_followup()
            return
        if self._is_alive(captain):
            self._launch_badge_followup()
            return
        player.skill['badge_action_taken'] = False
        self.day_state['phase'] = 'badge_transfer'
        self.stage = GameStage.BADGE_TRANSFER
        self.day_state['badge_transfer_action_done'] = False
        self.day_state['badge_transfer_timer_elapsed'] = False
        self._schedule_badge_transfer_timer()
        self.broadcast_msg(f'{self._format_label(captain)}需要移交或撕毁警徽。')

    def _complete_badge_transfer_phase(self):
        if self.stage != GameStage.BADGE_TRANSFER:
            return
        self.day_state['badge_transfer_action_done'] = True
        self._try_finalize_badge_transfer_phase()

    def _schedule_badge_transfer_timer(self, seconds: int = 10):
        task = self.day_state.get('badge_transfer_timer_task')
        if task:
            task.cancel()
        self.day_state['badge_transfer_timer_task'] = asyncio.create_task(
            self._badge_transfer_timer(seconds)
        )

    async def _badge_transfer_timer(self, seconds: int):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self.day_state['badge_transfer_timer_task'] = None
        self.day_state['badge_transfer_timer_elapsed'] = True
        if self.stage != GameStage.BADGE_TRANSFER:
            return
        if not self.day_state.get('badge_transfer_action_done'):
            captain_nick = self.skill.get('sheriff_captain')
            player = self.players.get(captain_nick) if captain_nick else None
            if player and not player.skill.get('badge_action_taken', False):
                self.handle_sheriff_badge_action(player, 'destroy')
                return
        self._try_finalize_badge_transfer_phase()

    def _try_finalize_badge_transfer_phase(self):
        if self.stage != GameStage.BADGE_TRANSFER:
            return
        if not (
            self.day_state.get('badge_transfer_action_done') and
            self.day_state.get('badge_transfer_timer_elapsed')
        ):
            return
        self._finalize_badge_transfer_phase()

    def _finalize_badge_transfer_phase(self):
        task = self.day_state.pop('badge_transfer_timer_task', None)
        if task:
            task.cancel()
        self.day_state.pop('badge_transfer_action_done', None)
        self.day_state.pop('badge_transfer_timer_elapsed', None)
        self.stage = None
        self.day_state['phase'] = 'badge_transfer_done'
        self._launch_badge_followup()

    def _launch_badge_followup(self):
        followup = self.day_state.pop('pending_badge_followup', None)
        if not followup:
            return
        queue = [nick for nick in followup.get('queue', []) if nick in self.players]
        allow_speech = followup.get('allow_speech', False)
        after_stage = followup.get('after_stage', 'exile_speech')
        if allow_speech and queue:
            if followup.get('disable_skill_prompt', False):
                self.broadcast_msg('所有今日死亡玩家进入遗言阶段')
            self.start_last_words(
                queue,
                allow_speech=True,
                after_stage=after_stage,
                randomize=followup.get('randomize', False),
                skip_first_skill_msg=followup.get('skip_first_skill_msg', False),
                disable_skill_prompt=followup.get('disable_skill_prompt', False)
            )
        else:
            self._resolve_post_death_after_stage(after_stage)

    def _resolve_post_death_after_stage(self, after_stage: str):
        if after_stage == 'exile_speech':
            self.prompt_sheriff_order()
        elif after_stage == 'end_day':
            self._finalize_day_execution()
        elif after_stage == 'announcement':
            self._resume_day_announcement()

    def _resume_day_announcement(self):
        if self._trigger_pending_day_bomb_flow():
            return
        self.stage = GameStage.Day
        self.day_state['phase'] = 'announcement'
        if self.day_state.pop('pending_announcement_broadcast', False):
            self.broadcast_msg('请房主公布昨夜信息')

    def _finalize_day_execution(self):
        day_deaths = self.day_state.get('day_deaths')
        if day_deaths:
            for nick in day_deaths:
                player = self.players.get(nick)
                if player:
                    player.status = PlayerStatus.DEAD
        else:
            exec_target = self.day_state.get('pending_execution')
            if exec_target:
                player = self.players.get(exec_target)
                if player:
                    player.status = PlayerStatus.DEAD
        self.end_day_phase()
        self._schedule_victory_check()

    def _handle_idiot_flip(self, player: User):
        player.skill['idiot_flipped'] = True
        player.skill['idiot_vote_banned'] = True
        player.status = PlayerStatus.ALIVE
        self.day_state['pending_execution'] = None
        day_deaths = self.day_state.get('day_deaths') or []
        if player.nick in day_deaths:
            self.day_state['day_deaths'] = [n for n in day_deaths if n != player.nick]
        else:
            self.day_state['day_deaths'] = day_deaths
        self.broadcast_msg(f"{self._format_label(player.nick)}翻牌为白痴，免除本次放逐。")
        if self.skill.get('sheriff_captain') == player.nick:
            player.skill['idiot_badge_transfer_required'] = True
            self.broadcast_msg('请移交警徽。')
        self.start_last_words(
            [player.nick],
            allow_speech=True,
            after_stage='end_day',
            randomize=False,
            skip_first_skill_msg=True,
            disable_skill_prompt=True
        )

    def end_day_phase(self):
        self.day_state['phase'] = 'done'
        self.day_state['pending_execution'] = None
        self.day_state['night_deaths'] = []
        self.day_state['day_deaths'] = []
        self.day_state['night_anchor'] = None
        self.day_state['sheriff_order_pending'] = False
        self.day_state['pending_badge_followup'] = None
        self.stage = GameStage.Day
        self.broadcast_msg('白天结束，进入夜晚')
        for user in self.players.values():
            user.skill['exile_vote_pending'] = False
            user.skill['exile_has_balloted'] = False
            user.skill['last_words_skill_resolved'] = False
            user.skill['last_words_done'] = False
            user.skill['pending_last_skill'] = False
            user.skill.pop('badge_action_taken', None)
        self.skill.pop('pending_day_bombs', None)
        captain = self.skill.get('sheriff_captain')
        if captain and not self._is_alive(captain):
            self.skill['sheriff_captain'] = None

    @classmethod
    def get(cls, room_id) -> Optional['Room']:
        return Global.get_room(room_id)

    @classmethod
    def validate_room_join(cls, room_id):
        room = cls.get(room_id)
        if not room: return '房间不存在'
        if room.is_full(): return '房间已满'

    @classmethod
    def alloc(cls, room_setting) -> 'Room':
        roles = []
        roles.extend([Role.WOLF] * room_setting['wolf_num'])
        roles.extend([Role.CITIZEN] * room_setting['citizen_num'])
        roles.extend(Role.from_option(room_setting['god_wolf']))
        roles.extend(Role.from_option(room_setting['god_citizen']))

        return Global.reg_room(
            cls(
                id=None,
                roles=copy(roles),
                witch_rule=WitchRule.from_option(room_setting['witch_rule']),
                guard_rule=GuardRule.from_option(room_setting['guard_rule']),
                sheriff_bomb_rule=SheriffBombRule.from_option(
                    room_setting.get('sheriff_bomb_rule', SheriffBombRule.DOUBLE_LOSS.value)
                ),
                started=False,
                roles_pool=copy(roles),
                players=dict(),
                round=0,
                stage=None,
                waiting=False,
                log=list(),
                skill=dict(),
                logic_thread=None,
                game_over=False,
                death_pending=[],
                day_state=dict(),
                current_speaker=None,
                sheriff_speakers=None,
                sheriff_speaker_index=0,
            )
        )
