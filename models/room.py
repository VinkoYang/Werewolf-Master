# models/room.py
import asyncio
import random
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Union, Any

from pywebio.session import run_async
from pywebio.session.coroutinebased import TaskHandler

from enums import Role, WitchRule, GuardRule, GameStage, LogCtrl, PlayerStatus
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

role_classes = {
    Role.CITIZEN: Citizen,
    Role.WOLF: Wolf,
    Role.WOLF_KING: WolfKing,
    Role.SEER: Seer,
    Role.WITCH: Witch,
    Role.GUARD: Guard,
    Role.HUNTER: Hunter,
    Role.DREAMER: Dreamer,
}

@dataclass
class Room:
    id: Optional[int] = None
    roles: List[Role] = field(default_factory=list)
    witch_rule: WitchRule = WitchRule.SELF_RESCUE_FIRST_NIGHT_ONLY
    guard_rule: GuardRule = GuardRule.MED_CONFLICT

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

        # ---------- 狼人 ----------
        self.stage = GameStage.WOLF
        for user in self.players.values():
            user.skill['acted_this_stage'] = False
        self.broadcast_msg('狼人请出现', tts=True)
        await asyncio.sleep(1)
        
        # 发送狼队成员信息给所有狼人
        wolf_players = [u for u in self.players.values() if u.role in (Role.WOLF, Role.WOLF_KING) and u.status == PlayerStatus.ALIVE]
        if wolf_players:
            wolf_info_parts = []
            for wolf in wolf_players:
                if wolf.role == Role.WOLF_KING:
                    wolf_info_parts.append(f"{wolf.seat}号(狼王)")
                else:
                    wolf_info_parts.append(f"{wolf.seat}号")
            
            wolf_info = "狼人玩家是：" + "、".join(wolf_info_parts)
            
            # 发送给所有狼人
            for u in wolf_players:
                self.send_msg(wolf_info, nick=u.nick)
        
        await asyncio.sleep(2)
        
        self.waiting = True
        await self.wait_for_player()

        # 统一结算狼人击杀（统计票数，最多票者为今晚被刀）
        wolf_votes = self.skill.get('wolf_votes', {})
        kill_target = None
        
        if wolf_votes:
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
                if u.role in (Role.WOLF, Role.WOLF_KING):
                    self.send_msg(f"今夜，狼队选择{target_seat}号玩家被击杀。", nick=u.nick)
            
            # 清理投票记录
            if 'wolf_votes' in self.skill:
                del self.skill['wolf_votes']
            # 清理玩家临时选择
            for u in self.players.values():
                u.skill.pop('wolf_choice', None)
        else:
            # d. 所有狼人都没有选择或点击了"放弃" -> 空刀
            for u in self.players.values():
                if u.role in (Role.WOLF, Role.WOLF_KING):
                    self.send_msg("今夜，狼队空刀。", nick=u.nick)

        # 延迟3秒后再显示"狼人请闭眼"
        await asyncio.sleep(3)
        self.broadcast_msg('狼人请闭眼', tts=True)
        await asyncio.sleep(2)

        # ---------- 其他神职 ----------
        night_roles = [
            (GameStage.SEER, [Role.SEER]),
            (GameStage.WITCH, [Role.WITCH]),
            (GameStage.GUARD, [Role.GUARD]),
            (GameStage.HUNTER, [Role.HUNTER]),
            (GameStage.DREAMER, [Role.DREAMER]),
        ]

        for stage, role_list in night_roles:
            if self._has_active_role(role_list):
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
                
                self.waiting = True
                await self.wait_for_player()
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

            if u.status == PlayerStatus.PENDING_POISON:
                if not immunity:
                    u.status = PlayerStatus.DEAD
                    dead_this_night.append(u.nick)
                    if u.role in (Role.HUNTER, Role.WOLF_KING):
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

        self.death_pending = dead_this_night
        self.broadcast_msg('天亮请睁眼', tts=True)
        await asyncio.sleep(2)
        if self.round == 1:
            self.stage = GameStage.SHERIFF
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
                # 每个玩家使用自己的session发送取消事件
                if user.main_task_id and hasattr(user, 'session') and user.session:
                    try:
                        user.session.send_client_event({
                            'event': 'from_cancel',
                            'task_id': user.main_task_id,
                            'data': None
                        })
                    except Exception:
                        pass
            except Exception:
                pass

    async def check_game_end(self):
        wolves = [u for u in self.list_alive_players() if u.role in (Role.WOLF, Role.WOLF_KING)]
        goods = [u for u in self.list_alive_players() if u.role not in (Role.WOLF, Role.WOLF_KING)]
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

    def _format_label(self, nick: str) -> str:
        player = self.players.get(nick)
        if not player:
            return nick
        seat = player.seat or '?'
        return f"{seat}号{player.nick}"

    def get_active_sheriff_candidates(self) -> List[str]:
        state = self.sheriff_state or {}
        if not state:
            return []
        base = state.get('pk_candidates') if state.get('phase') in ('pk_speech', 'await_pk_vote', 'pk_vote') and state.get('pk_candidates') else state.get('up', [])
        active = [nick for nick in base if nick not in state.get('withdrawn', []) and self._is_alive(nick)]
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

        alive = self._alive_nicks()
        if all(self.players[n].skill.get('sheriff_voted', False) for n in alive):
            up_list = state['up']
            msg = '上警的玩家有：' + ('、'.join(self._format_label(n) for n in up_list) if up_list else '无人')
            self.broadcast_msg(msg)

            alive_count = len(alive)
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

    def _check_auto_elect(self):
        state = self.sheriff_state or {}
        if state.get('phase') not in ('speech', 'await_vote'):
            return
        candidates = self.get_active_sheriff_candidates()
        if len(candidates) == 1:
            self._declare_sheriff(candidates[0])

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
            eligible = [u.nick for u in self.list_alive_players() if u.nick not in candidates]
            prompt = '请非PK玩家在10秒内完成PK投票'
        else:
            eligible = [nick for nick in state.get('down', []) if self._is_alive(nick)]
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

    def record_sheriff_ballot(self, user: User, target: str):
        state = self.sheriff_state or {}
        if state.get('phase') not in ('vote', 'pk_vote'):
            return
        if user.nick not in state.get('eligible_voters', []):
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

    def start_pk_speech(self):
        state = self.sheriff_state or {}
        candidates = [nick for nick in state.get('pk_candidates', []) if self._is_alive(nick)]
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
            self.prepare_day_phase()

    # -------------------- 白天阶段逻辑 --------------------
    def prepare_day_phase(self):
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
        }
        self.broadcast_msg('请房主公布昨夜信息')

    async def publish_night_info(self) -> Optional[str]:
        state = self.day_state or {}
        if state.get('phase') != 'announcement':
            return '当前不需要公布'

        death_list = [nick for nick in self.death_pending if nick in self.players]
        formatted = [self._format_label(nick) for nick in death_list]

        if not death_list:
            self.broadcast_msg('昨夜平安夜，无人出局。')
            self.broadcast_msg('请警长选择发言顺序')
            self.start_exile_speech()
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
            self.start_last_words(
                speech_order,
                allow_speech=allow_speech,
                after_stage='exile_speech',
                randomize=False,
                skip_first_skill_msg=skip_msg
            )

        self.death_pending = []

    def start_last_words(self, queue: List[str], allow_speech: bool, after_stage: str, randomize: bool = False, skip_first_skill_msg: bool = False):
        valid_queue = [nick for nick in queue if nick in self.players]
        if randomize and len(valid_queue) > 1:
            random.shuffle(valid_queue)
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
        self.stage = GameStage.LAST_WORDS
        for nick in valid_queue:
            player = self.players.get(nick)
            if not player:
                continue
            player.skill['last_words_skill_resolved'] = False
            player.skill['last_words_done'] = False
            player.skill['pending_last_skill'] = False
        self.day_state['last_word_skill_announced'] = skip_first_skill_msg
        self.day_state['last_word_speech_announced'] = False
        if not skip_first_skill_msg:
            self._announce_last_word_skill()

    def handle_last_word_skill_choice(self, user: User, choice: str):
        if self.day_state.get('phase') != 'last_words':
            return
        if user.nick != self.day_state.get('current_last_word'):
            return
        if choice == '发动技能':
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
            if not self.day_state.get('last_word_speech_announced', False):
                self.broadcast_msg(f'请{self._format_label(user.nick)}发表遗言')
                self.day_state['last_word_speech_announced'] = True
            return
        queue = self.day_state.get('last_words_queue', [])
        if queue and queue[0] == user.nick:
            queue.pop(0)
        if queue:
            self.day_state['current_last_word'] = queue[0]
            self.day_state['last_word_skill_announced'] = False
            self.day_state['last_word_speech_announced'] = False
            self._announce_last_word_skill()
            player = self.players.get(queue[0])
            if player:
                player.skill['last_words_skill_resolved'] = False
                player.skill['last_words_done'] = False
                player.skill['pending_last_skill'] = False
        else:
            next_stage = self.day_state.get('after_last_words')
            if next_stage == 'exile_speech':
                self.start_exile_speech()
            elif next_stage == 'end_day':
                exec_target = self.day_state.get('pending_execution')
                if exec_target:
                    player = self.players.get(exec_target)
                    if player:
                        player.status = PlayerStatus.DEAD
                self.end_day_phase()

    def _announce_last_word_skill(self):
        current = self.day_state.get('current_last_word')
        if not current:
            return
        if not self.day_state.get('last_word_skill_announced', False):
            self.broadcast_msg(f'等待{self._format_label(current)}发动技能')
            self.day_state['last_word_skill_announced'] = True
            self.day_state['last_word_speech_announced'] = False

    def start_exile_speech(self):
        alive = sorted(self.list_alive_players(), key=lambda u: u.seat or 0)
        queue = [u.nick for u in alive]
        if not queue:
            self.end_day_phase()
            return
        self.day_state['phase'] = 'exile_speech'
        self.day_state['exile_speech_queue'] = queue
        self.stage = GameStage.EXILE_SPEECH
        self.current_speaker = queue[0]
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
            eligible = [u.nick for u in self.list_alive_players() if u.nick not in candidates]
            stage = GameStage.EXILE_PK_VOTE
            new_phase = 'exile_pk_vote'
        else:
            candidates = [u.nick for u in self.list_alive_players()]
            eligible = [u.nick for u in self.list_alive_players()]
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
        for nick in candidates:
            voters = records.get(nick, [])
            seats = '、'.join(self._format_label(v) for v in voters) if voters else '无'
            self.broadcast_msg(f"{self._format_label(nick)}得票：{seats}")
        abstain = records.get('弃票', [])
        if abstain:
            seats = '、'.join(self._format_label(v) for v in abstain)
            self.broadcast_msg(f"弃票：{seats}")
        tally = {nick: len(records.get(nick, [])) for nick in candidates}
        if not tally:
            self.broadcast_msg('无人投票，白天结束')
            self.end_day_phase()
            return
        max_votes = max(tally.values())
        winners = [nick for nick, cnt in tally.items() if cnt == max_votes]
        if len(winners) == 1:
            self.start_execution_sequence(winners[0])
        else:
            if phase == 'exile_vote':
                self.day_state['pk_candidates'] = winners
                self.start_exile_pk_speech()
            else:
                self.broadcast_msg('PK 投票仍旧平票，无人出局')
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
        target.status = PlayerStatus.PENDING_DEAD
        self.day_state['pending_execution'] = nick
        self.broadcast_msg(f"{self._format_label(nick)}被放逐，进入被动技能与遗言阶段")
        self.start_last_words([nick], allow_speech=True, after_stage='end_day')

    def end_day_phase(self):
        self.day_state['phase'] = 'done'
        self.day_state['pending_execution'] = None
        self.stage = GameStage.Day
        self.broadcast_msg('白天结束，进入夜晚')
        for user in self.players.values():
            user.skill['exile_vote_pending'] = False
            user.skill['exile_has_balloted'] = False
            user.skill['last_words_skill_resolved'] = False
            user.skill['last_words_done'] = False
            user.skill['pending_last_skill'] = False

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
