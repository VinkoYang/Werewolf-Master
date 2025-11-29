"""Daytime, badge transfer, and last-words flows for Room runtime."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Dict, List, Optional

from enums import GameStage, PlayerStatus, Role
from roles.wolf_beauty import WolfBeauty

if TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from models.user import User
    from models.room import Room


class DaytimeFlowMixin:
    """Encapsulates day-phase mechanics extracted from Room."""

    def prepare_day_phase(self: 'Room', announce: bool = True) -> None:
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

    async def publish_night_info(self: 'Room') -> Optional[str]:
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
        self: 'Room',
        queue: List[str],
        allow_speech: bool,
        after_stage: str,
        randomize: bool = False,
        skip_first_skill_msg: bool = False,
        disable_skill_prompt: bool = False
    ) -> None:
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

    def handle_last_word_skill_choice(self: 'Room', user: 'User', choice: str) -> None:
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

    def complete_last_word_speech(self: 'Room', user: 'User') -> None:
        if self.day_state.get('phase') != 'last_words':
            return
        if user.nick != self.day_state.get('current_last_word'):
            return
        user.skill['last_words_done'] = True
        self._advance_last_words_if_ready(user)

    def advance_last_words_progress(self: 'Room', user: 'User') -> None:
        self._advance_last_words_if_ready(user)

    def _advance_last_words_if_ready(self: 'Room', user: 'User') -> None:
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
            self.day_state['last_words_skill_announced'] = skip_skill_prompt
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

    def _announce_last_word_skill(self: 'Room') -> None:
        current = self.day_state.get('current_last_word')
        if not current:
            return
        if not self.day_state.get('last_word_skill_announced', False):
            self.broadcast_msg(f'等待{self._format_label(current)}发动技能')
            self.day_state['last_word_skill_announced'] = True
            self.day_state['last_word_speech_announced'] = False

    def _prompt_current_last_word_speech(self: 'Room') -> None:
        current = self.day_state.get('current_last_word')
        if not current:
            return
        if not self.day_state.get('last_word_speech_announced', False):
            self.broadcast_msg(f'请{self._format_label(current)}发表遗言')
            self.day_state['last_word_speech_announced'] = True
            self.day_state['last_word_skill_announced'] = True

    def _sanitize_last_words_queue(self: 'Room', queue: List[str], randomize: bool) -> List[str]:
        valid_queue = [nick for nick in queue if nick in self.players]
        if randomize and len(valid_queue) > 1:
            random.shuffle(valid_queue)
        return valid_queue

    def _prepare_last_words_player(self: 'Room', player: Optional['User'], disable_skill_prompt: bool) -> None:
        if not player:
            return
        supports_skill = self._player_supports_last_skill(player)
        if disable_skill_prompt or not supports_skill:
            player.skill['last_words_skill_resolved'] = True
        else:
            player.skill['last_words_skill_resolved'] = False
        player.skill['last_words_done'] = False
        player.skill['pending_last_skill'] = False

    def _player_supports_last_skill(self: 'Room', player: Optional['User']) -> bool:
        if not player or not player.role_instance:
            return False
        supports = getattr(player.role_instance, 'supports_last_skill', None)
        if supports is None:
            return False
        return bool(supports())

    def _mark_followup_skills_resolved(self: 'Room') -> None:
        followup = self.day_state.get('pending_badge_followup')
        if not followup:
            return
        followup['disable_skill_prompt'] = True
        followup['skip_first_skill_msg'] = True

    def _kickoff_last_word_prompt(self: 'Room') -> None:
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

    def _schedule_victory_check(self: 'Room') -> None:
        if self.game_over:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        loop.create_task(self.check_game_end())

    def prompt_sheriff_order(self: 'Room') -> None:
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

    def set_sheriff_order(self: 'Room', user: 'User', choice: str, auto: bool = False) -> Optional[str]:
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

    def force_sheriff_order_random(self: 'Room') -> None:
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

    def _random_queue_without_sheriff(self: 'Room') -> tuple[List[str], Optional['User'], Optional[str]]:
        alive = self.list_alive_players()
        if not alive:
            return [], None, None
        start_player = random.choice(alive)
        direction = random.choice(['asc', 'desc'])
        queue = self._build_queue_from_player(start_player.nick, direction)
        return queue, start_player, direction

    def _build_queue_from_player(self: 'Room', start_nick: str, direction: str) -> List[str]:
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

    def _build_directional_queue(self: 'Room', direction: str) -> List[str]:
        """Return all alive players ordered ascending/descending by seat."""
        alive = sorted(self.list_alive_players(), key=lambda u: u.seat or 0)
        if not alive:
            return []
        if direction == 'desc':
            alive = list(reversed(alive))
        return [player.nick for player in alive]

    def _rotate_players(self: 'Room', players: List['User'], start_idx: int, step: int) -> List['User']:
        if not players:
            return []
        n = len(players)
        idx = start_idx % n
        ordered = []
        for _ in range(n):
            ordered.append(players[idx])
            idx = (idx + step) % n
        return ordered

    def start_exile_speech(self: 'Room', queue: Optional[List[str]] = None, announce: bool = True) -> None:
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

    def _announce_exile_speaker(self: 'Room', pk: bool) -> None:
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

    def advance_exile_speech(self: 'Room') -> None:
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

    def start_exile_vote(self: 'Room', pk_mode: bool = False) -> Optional[str]:
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

    def record_exile_vote(self: 'Room', user: 'User', target: str) -> None:
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

    def finish_exile_vote(self: 'Room') -> None:
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

    def start_exile_pk_speech(self: 'Room') -> None:
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

    def start_execution_sequence(self: 'Room', nick: str) -> None:
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

    def handle_last_word_skill_kill(self: 'Room', nick: str, from_day_execution: bool = False) -> None:
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

    def _set_badge_followup(
        self: 'Room',
        queue: List[str],
        allow_speech: bool,
        after_stage: str,
        randomize: bool = False,
        skip_first_skill_msg: bool = False,
        disable_skill_prompt: bool = False
    ) -> None:
        self.day_state['pending_badge_followup'] = {
            'queue': [nick for nick in queue if nick in self.players],
            'allow_speech': allow_speech,
            'after_stage': after_stage,
            'randomize': randomize,
            'skip_first_skill_msg': skip_first_skill_msg,
            'disable_skill_prompt': disable_skill_prompt,
        }

    def _start_badge_transfer_phase(self: 'Room') -> None:
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

    def _schedule_badge_transfer_timer(self: 'Room', seconds: int = 10) -> None:
        self._badge_timer.start(seconds, self._handle_badge_transfer_timeout)

    async def _handle_badge_transfer_timeout(self: 'Room') -> None:
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

    def _complete_badge_transfer_phase(self: 'Room') -> None:
        if self.stage != GameStage.BADGE_TRANSFER:
            return
        self.day_state['badge_transfer_action_done'] = True
        self._try_finalize_badge_transfer_phase()

    def _try_finalize_badge_transfer_phase(self: 'Room') -> None:
        if self.stage != GameStage.BADGE_TRANSFER:
            return
        if not (
            self.day_state.get('badge_transfer_action_done') and
            self.day_state.get('badge_transfer_timer_elapsed')
        ):
            return
        self._finalize_badge_transfer_phase()

    def _finalize_badge_transfer_phase(self: 'Room') -> None:
        self._badge_timer.cancel()
        self.day_state.pop('badge_transfer_action_done', None)
        self.day_state.pop('badge_transfer_timer_elapsed', None)
        self.stage = None
        self.day_state['phase'] = 'badge_transfer_done'
        self._launch_badge_followup()

    def _launch_badge_followup(self: 'Room') -> None:
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

    def _resolve_post_death_after_stage(self: 'Room', after_stage: str) -> None:
        if after_stage == 'exile_speech':
            self.prompt_sheriff_order()
        elif after_stage == 'end_day':
            self._finalize_day_execution()
        elif after_stage == 'announcement':
            self._resume_day_announcement()

    def _resume_day_announcement(self: 'Room') -> None:
        if self._trigger_pending_day_bomb_flow():
            return
        self.stage = GameStage.Day
        self.day_state['phase'] = 'announcement'
        if self.day_state.pop('pending_announcement_broadcast', False):
            self.broadcast_msg('请房主公布昨夜信息')

    def handle_sheriff_badge_action(self: 'Room', user: 'User', choice: str) -> Optional[str]:
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

    def handle_idiot_badge_transfer(self: 'Room', user: 'User', target_nick: Optional[str]) -> Optional[str]:
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

    def _finalize_day_execution(self: 'Room') -> None:
        day_deaths = self.day_state.get('day_deaths')
        extra_charmed: List[str] = []
        if day_deaths:
            for nick in day_deaths:
                player = self.players.get(nick)
                if player:
                    player.status = PlayerStatus.DEAD
                    # 处理狼美人殉情
                    if player.role == Role.WOLF_BEAUTY:
                        charmed = WolfBeauty.handle_wolf_beauty_death(self, player)
                        if charmed:
                            extra_charmed.append(charmed)
        else:
            exec_target = self.day_state.get('pending_execution')
            if exec_target:
                player = self.players.get(exec_target)
                if player:
                    player.status = PlayerStatus.DEAD
                    if player.role == Role.WOLF_BEAUTY:
                        charmed = WolfBeauty.handle_wolf_beauty_death(self, player)
                        if charmed:
                            extra_charmed.append(charmed)

        if extra_charmed:
            day_deaths = self.day_state.setdefault('day_deaths', [])
            for nick in extra_charmed:
                if nick not in day_deaths:
                    day_deaths.append(nick)
        self.update_nine_tailed_state()
        self.end_day_phase()
        self._schedule_victory_check()

    def _handle_idiot_flip(self: 'Room', player: 'User') -> None:
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

    def end_day_phase(self: 'Room') -> None:
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
