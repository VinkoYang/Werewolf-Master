# Runtime Mixins & Flow Refactor (2025-11-25)

This document captures the structural changes introduced while extracting the in-game runtime logic out of `models/room.py`. The goal of the refactor is to separate pre-game orchestration (room management, lobby wiring) from phase-specific gameplay flows so that day/night routines can evolve independently.

## Why the refactor?
- `room.py` exceeded several thousand lines and mixed lobby setup with sheriff/daytime handling, making regressions easy.
- Several bugs (e.g., sheriff speech order, badge transfer timing) stemmed from duplicated state handling scattered across helpers.
- Board presets needed a stable base class that was not tied to legacy imports.

## Module map
| Module | Responsibility |
| --- | --- |
| `models/room_runtime.RoomRuntimeMixin` | Composes `SheriffFlowMixin` and `DaytimeFlowMixin`, instantiates timers, exposes helpers like `_ensure_game_config`, `_alive_nicks`, `_can_player_vote`, and proxies to the configured `BaseGameConfig` class. `Room` now mixes this in and only keeps lobby/pre-game logic. |
| `models/runtime/sheriff.py (SheriffFlowMixin)` | Handles the full sheriff lifecycle: signups, speeches/PK flows, bomb handling (including `SheriffBombRule` nuances), timers for deferred withdrawals and vote deadlines, badge transfer triggers, and checkpoint resume helpers. |
| `models/runtime/daytime.py (DaytimeFlowMixin)` | Owns daytime announcements, last words queues, badge transfer follow-ups, exile speeches/pk/votes, execution flows, sheriff speech order prompts, and white wolf king bomb chaining. |
| `models/runtime/tools.py` | Lightweight `AsyncTimer`, `VoteTimer`, and `BadgeTransferTimer` utilities shared by the mixins (instead of bespoke `asyncio.create_task` snippets baked in the room). |
| `presets/base.py` | New canonical home of `BaseGameConfig` / `DefaultGameFlow`. All presets import from here; `presets/game_config_base.py` stays as a shim for legacy imports until downstream users migrate. |

## Integration details
1. **Room composition**: `Room` subclasses now inherit from `RoomRuntimeMixin`. The mixin sets up vote/badge timers and lazily instantiates the right `BaseGameConfig` implementation via `presets.game_config_registry.resolve_game_config_class`.
2. **Config helpers**: Methods such as `_ensure_game_config`, `_has_active_role`, `_ensure_half_blood_choices`, and victory checks were moved out of `room.py`. Presets continue to call into the same logic but via the mixin proxy.
3. **Sheriff/daytime state**: Shared helpers (`_alive_nicks`, `_sheriff_signup_pool`, `_build_directional_queue`, `_random_queue_without_sheriff`, etc.) now live close to the flows that consume them, reducing the risk of stale copies.
4. **Timers**: Sheriff voting/withdraw flows and badge transfers now lean on `VoteTimer`/`BadgeTransferTimer`, giving us consistent cancellation semantics and easier debugging.
5. **Compatibility**: Third-party code importing from `presets.game_config_base` will keep working (shim simply re-exports from `.base`). New code should import from `presets.base` directly.

## Behavior and UX tweaks bundled with the refactor
- **Sheriff speech order**: `DaytimeFlowMixin._build_directional_queue()` ensures both manual (警长决定) and automatic/random (无警长或超时) queues always include every alive seat exactly once, eliminating the previously reported AttributeError.
- **Nine-tailed fox**: `roles/nine_tailed_fox.py` now sends a private 🌙 notification with the current tail count the first time the role wakes each night, and resets the prompt flag when the player acknowledges.
- **Wolf phase telemetry**: `roles/wolf.py` switches the action gate from `acted_this_stage` to a dedicated `wolf_action_done` flag so wolves can toggle targets without unblocking the room prematurely. Stage cleanup clears the new flag after votes resolve.

## Testing & follow-up checklist
- [x] Manual smoke test: start a 12p board, confirm day/night loop runs with the new mixins.
- [x] Sheriff flow: cover signup → speech → vote, PK fallback, and a forced timeout to ensure timers resolve.
- [x] Badge transfer: verify standard death handoff, idiot flip, and badge destruction path when timeout hits.
- [x] Wolf `放弃` handling: verify all wolves can abstain and the room still proceeds to night summary.
- [ ] Nine-tailed fox notification: ensure the 🌙 message appears only once per night per fox.

If new regressions surface, update this document with the scenario and corresponding fix reference.

---

## In-process simulation framework (`tests/simulate_12p.py`) — 2026-06-22

A serverless auto-play harness was added so the full game loop can be exercised without running `server.py`.

### How it works

The script monkey-patches all modules that bind `async_sleep` via `from utils import async_sleep` before the game modules are imported:

```
utils, presets.base, models.room, models.runtime.tools, models.runtime.sheriff
```

Each of those five modules holds its own reference to the original function; patching only `utils.async_sleep` would leave the others intact. All five are patched to `min(seconds, 0.01)` and restored after the run.

`DefaultGameFlow.wait_for_player` is also patched to force `min_duration=0`, bypassing the 20-second night floor.

`AutoWatcher` polls `room.waiting` / `room.sheriff_state` / `room.day_state` in a tight async loop and dispatches to role-specific handlers, covering wolf kills, seer checks, witch heal/poison, guard protect, hunter night confirm, and hunter daytime shoot.

### Known tricky interactions discovered during development

| Scenario | Root cause | Fix |
|---|---|---|
| Game stuck at WITCH/GUARD | `from utils import async_sleep` creates separate bindings per module; patching `utils` alone doesn't reach them | Patch all five modules explicitly |
| Game stuck at LAST_WORDS (hunter exiled) | `complete_last_word_speech` returns early when `last_words_skill_resolved=False`; must call `handle_last_word_skill_choice('放弃技能')` first | Call skill-choice before speech |
| Sheriff signup stuck after night-1 wolf kill | `_sheriff_signup_pool()` includes `PENDING_DEAD` players; watcher must vote for them too | Iterate `_sheriff_signup_pool()` not `alive_players()` |
| Hunter shoot target appears in next night's death announcement | `from_day_execution` condition in `confirm_shoot` used `'day_skill_to_speech'` which is never set → always `False` → target stays `PENDING_DEAD` | Changed to `bool(room.day_state.get('pending_execution'))` (see bug fix below) |

---

## Bug fix: hunter/wolf_king `from_day_execution` — 2026-06-22

**Files:** `roles/hunter.py`, `roles/wolf_king.py`

**Symptom:** When a hunter (or wolf king) is exiled and shoots a player during the daytime last-words cycle, the shot target appeared in the *next night's* death announcement (e.g. "昨夜1号和…死亡" when 1号 had already been killed the previous day).

**Root cause:**

`confirm_shoot` computed:
```python
from_day_execution = (
    room.stage == GameStage.LAST_WORDS and
    room.day_state.get('after_last_words') == 'day_skill_to_speech'
)
```

The value `'day_skill_to_speech'` is never written anywhere in the codebase, so `from_day_execution` was permanently `False`.

`handle_last_word_skill_kill(from_day_execution=False)` does **not** add the target to `day_deaths`, leaving the target's status as `PENDING_DEAD`. The next night's death-resolution loop in `presets/base.py::night_logic()` sees `PENDING_DEAD` and treats it as a new night kill.

**Fix:**

```python
from_day_execution = (
    room.stage == GameStage.LAST_WORDS and
    bool(room.day_state.get('pending_execution'))
)
```

`pending_execution` is set by `start_execution_sequence()` (daytime exile) and cleared by `end_day_phase()`, making it a reliable sentinel for the daytime-execution context. With `from_day_execution=True`, the target is added to `day_deaths` and correctly finalized to `DEAD` via `_finalize_day_execution()` on the same day.
