"""Sheriff election, withdrawal, and wolf bomb flows."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Dict, List, Optional

from enums import GameStage, PlayerStatus, Role, SheriffBombRule
from presets.base import WOLF_TEAM_ROLES

if TYPE_CHECKING:  # pragma: no cover - used for typing only
    from models.user import User
    from models.room import Room


class SheriffFlowMixin:
    """Mix-in hosting sheriff election logic extracted from Room."""

    def init_sheriff_phase(self: 'Room') -> None:
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

    def get_active_sheriff_candidates(self: 'Room') -> List[str]:
        state = self.sheriff_state or {}
        if not state:
            return []
        base = state.get('pk_candidates') if state.get('phase') in ('pk_speech', 'await_pk_vote', 'pk_vote') and state.get('pk_candidates') else state.get('up', [])
        active = [nick for nick in base if nick not in state.get('withdrawn', []) and self._is_sheriff_eligible(nick)]
        return active

    def record_sheriff_choice(self: 'Room', user: 'User', choice: str) -> None:
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

    def start_sheriff_speeches(self: 'Room') -> None:
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

    def _announce_next_speaker(self: 'Room', is_pk: bool) -> None:
        state = self.sheriff_state or {}
        queue = state.get('speech_queue', [])
        if not queue:
            return
        current = queue[0]
        upcoming = queue[1] if len(queue) > 1 else None
        order_dir = state['pk_order_dir'] if is_pk else state.get('order_dir')
        order_label = '逆序' if order_dir == 'desc' else '顺序'
        prefix = '进行平票PK发言' if is_pk else '进行警长竞选发言'
        if upcoming:
            self.broadcast_msg(f"{prefix}，请{self._format_label(current)}发言，{order_label}发言顺序，{self._format_label(upcoming)}请准备。")
        else:
            self.broadcast_msg(f"{prefix}，请{self._format_label(current)}发言，{order_label}发言顺序。")

    def advance_sheriff_speech(self: 'Room', finished_nick: str) -> None:
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

    def handle_sheriff_withdraw(self: 'Room', user: 'User') -> Optional[str]:
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

    def can_wolf_self_bomb(self: 'Room', user: 'User') -> bool:
        if not user or user.status != PlayerStatus.ALIVE:
            return False
        if user.role not in WOLF_TEAM_ROLES:
            return False
        if self.stage == GameStage.SPEECH:
            state = self.sheriff_state or {}
            return state.get('phase') in ('speech', 'pk_speech')
        if self.stage in (GameStage.EXILE_SPEECH, GameStage.EXILE_PK_SPEECH):
            day_phase = (self.day_state or {}).get('phase')
            return day_phase in ('exile_speech', 'exile_pk_speech')
        state = self.sheriff_state or {}
        if self.stage == GameStage.SHERIFF and state.get('phase') == 'deferred_withdraw':
            return True
        return False

    def handle_wolf_self_bomb(self: 'Room', user: 'User') -> Optional[str]:
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

    def _handle_exile_stage_bomb(self: 'Room', user: 'User') -> None:
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

    def _handle_sheriff_stage_bomb(self: 'Room', user: 'User', deferred: bool) -> None:
        state = self.sheriff_state or {}
        self.broadcast_msg(f"{self._format_label(user.nick)}在警长竞选阶段自曝，竞选被迫中止。")
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

    def _prepare_white_wolf_bomb(self: 'Room', user: 'User') -> Optional['User'] | str:
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

    def _execute_white_wolf_bomb(self: 'Room', user: 'User', target: Optional['User']) -> None:
        if not target:
            return
        if target.status != PlayerStatus.ALIVE:
            user.send_msg('目标已不在场，额外击杀失效。')
            user.skill.pop('white_wolf_bomb_target', None)
            return
        self.broadcast_msg(f"{self._format_label(user.nick)}自曝，强制带走{self._format_label(target.nick)}。")
        self._enqueue_white_wolf_kill(target)
        user.skill.pop('white_wolf_bomb_target', None)

    def _queue_pending_day_bomb(self: 'Room', nick: str, origin: str) -> None:
        queue = self.skill.setdefault('pending_day_bombs', [])
        queue.append({'nick': nick, 'origin': origin})

    def _trigger_pending_day_bomb_flow(self: 'Room') -> bool:
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

    def _enqueue_white_wolf_kill(self: 'Room', target: Optional['User']) -> None:
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
        self: 'Room',
        nick: str,
        after_stage: str = 'end_day',
        include_bomber: bool = True,
        allow_followup_speech: bool = False,
        suppress_skill_prompt: bool = True,
        use_badge_followup: bool = True
    ) -> None:
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

    def _check_auto_elect(self: 'Room') -> None:
        state = self.sheriff_state or {}
        if state.get('phase') not in ('speech', 'await_vote'):
            return
        candidates = self.get_active_sheriff_candidates()
        if len(candidates) == 1:
            self._declare_sheriff(candidates[0])

    def resume_deferred_sheriff_phase(self: 'Room') -> None:
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

    def _start_deferred_withdraw_timer(self: 'Room', seconds: int = 10) -> None:
        self._cancel_deferred_withdraw_timer()
        task = asyncio.create_task(self._deferred_withdraw_timer(seconds))
        self.skill['deferred_withdraw_task'] = task

    def _cancel_deferred_withdraw_timer(self: 'Room') -> None:
        task = self.skill.pop('deferred_withdraw_task', None)
        if task:
            task.cancel()

    async def _deferred_withdraw_timer(self: 'Room', seconds: int) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        if self.sheriff_state.get('phase') == 'deferred_withdraw':
            self.complete_deferred_withdraw()

    def complete_deferred_withdraw(self: 'Room') -> None:
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

    def start_sheriff_vote(self: 'Room', pk_mode: bool = False) -> Optional[str]:
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

    def force_sheriff_abstain(self: 'Room', user: 'User', reason: str = 'timeout') -> bool:
        state = self.sheriff_state or {}
        if state.get('phase') not in ('vote', 'pk_vote'):
            return False
        if user.nick not in state.get('eligible_voters', []):
            return False
        if user.skill.get('sheriff_has_balloted'):
            return False
        if not self._can_player_vote(user.nick):
            return False

        self.record_sheriff_ballot(user, '弃票')
        message = None
        if reason == 'timeout':
            message = '⏱️ 超时未投票，系统视为弃票'
        elif reason == 'cancel':
            message = '你放弃了投票，系统视为弃票'
        elif isinstance(reason, str):
            message = reason
        if message:
            user.send_msg(message)
        return True

    def record_sheriff_ballot(self: 'Room', user: 'User', target: str) -> None:
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

    def finish_sheriff_vote(self: 'Room') -> None:
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

    def _start_sheriff_vote_timer(self: 'Room', seconds: int = 10) -> None:
        self._vote_timer.start(seconds, self._handle_sheriff_vote_timeout)

    def _cancel_sheriff_vote_timer(self: 'Room') -> None:
        self._vote_timer.cancel()

    async def _handle_sheriff_vote_timeout(self: 'Room') -> None:
        state = self.sheriff_state or {}
        if state.get('phase') not in ('vote', 'pk_vote'):
            return
        eligible = state.get('eligible_voters', [])
        timed_out = []
        for nick in eligible:
            player = self.players.get(nick)
            if player and self.force_sheriff_abstain(player, reason='timeout'):
                timed_out.append(nick)
        if timed_out:
            labels = '、'.join(self._format_label(nick) for nick in timed_out)
            self.broadcast_msg(f'{labels} 超时未投票，系统自动判为弃票。')
        self.finish_sheriff_vote()

    def start_pk_speech(self: 'Room') -> None:
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

    def _declare_sheriff(self: 'Room', nick: str) -> None:
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

    def finish_sheriff_phase(self: 'Room', winner: Optional[str]) -> None:
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
