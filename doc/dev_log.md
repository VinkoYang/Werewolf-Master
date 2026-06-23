# 开发日志

---

## V1.00 — FastAPI + Socket.IO 迁移

**完成内容**
- 整体框架从 PyWebIO 迁移至 FastAPI + Socket.IO，前后端通信改为全双工事件推送。
- 游戏状态持久保存于服务端，支持断线自动重连（reconnect_token + message_cursor 机制）。
- 角色逻辑重构为独立 `roles/` 模块，每个角色一个文件，继承 `RoleBase`。
- 夜晚流程与白天流程分离至 `DefaultGameFlow` 策略基类与 `DaytimeFlowMixin` / `SheriffFlowMixin`。
- 新增模拟测试框架（`tests/simulate_server.py`），通过 `_dispatch_action` 走完整按键分发路径，无需启动服务器即可验证游戏逻辑。

---

## 2026-06-23 机械狼 - 镜隐迷踪版型

**新增角色**

| 角色 | 别名 | 阵营 | 技能概要 |
|------|------|------|----------|
| 机械狼 | 觉醒隐狼 | 狼队（独立行动） | 每晚学习一名玩家身份并获得其技能；模仿狼人后获得一次性额外狼刀，其余狼人全部出局后方可使用 |
| 通灵师 | 魔镜少女 | 好人阵营 | 每晚查验一名玩家的具体身份，不可重复查验 |

**版型阵容**（12人）：机械狼 + 3普狼 + 4平民 + 通灵师 + 女巫 + 猎人 + 守卫

**夜晚顺序**：机械狼（首夜学习 / 后续夜行动）→ 普狼 → 守卫 → 通灵师 → 女巫 → 猎人

**机械狼阶段规则**
- 未持有技能（或首夜）：进入学习阶段（`MECHANICAL_WOLF_LEARN`），选择一名玩家学习其身份。
- 已持有技能（上一晚或更早学习）：进入行动阶段（`MECHANICAL_WOLF_ACT`），发动当前学到的技能。
- 两阶段均由同一个前置钩子 `handle_custom_pre_wolf_stages()` 动态调度，通过 `learned_night < room.round` 判断切换。

**关键规则细节**
- 机械狼**不参与**普狼讨论，普狼也不知道谁是机械狼（`MECHANICAL_WOLF` 不在 `WOLF_TEAM_ROLES`）。
- 额外狼刀在学到狼人/狼王时设置 `mw_wolf_knife_ready = True`（跨夜持久），但只有在 `WOLF_TEAM_ROLES` 全员出局后（`_all_other_wolves_dead()`）才允许使用，且仅限一次。
- 胜负判定使用 `WOLF_CAMP_ROLES`（含机械狼）而非 `WOLF_TEAM_ROLES`，单独重写 `check_game_end()`。
- `get_apparent_role()` 返回已学身份（供通灵师查验时显示伪装身份）。

**修复记录**
- 修复额外狼刀可在学习当晚立即发动的问题：从"当晚直接检查 learned_role"改为持久标志 + `_all_other_wolves_dead()` 双重门控。
- 补充 `learned_night = room.round` 写入 `confirm()` 的学习分支，确保 `handle_custom_pre_wolf_stages()` 中的阶段切换逻辑能正确运行。

**新增文件**

| 文件 | 说明 |
|------|------|
| `roles/mechanical_wolf.py` | 机械狼角色实现 |
| `roles/magic_mirror_girl.py` | 通灵师角色实现 |
| `presets/game_config_mechanical_wolf_mirror.py` | 版型配置与自定义游戏流程 |
| `doc/sim_result_standard12.md` | 机械狼版型模拟对局记录 |

**命名约定**
- 游戏内显示名（`Role.value`、`name` 字段、所有提示消息）统一使用**机械狼**和**通灵师**。
- 别名"觉醒隐狼"和"魔镜少女"仅出现在 `roles/` 文件 docstring 与 `doc/roles.md` 的角色介绍中作补充说明。

---

## 2026-06-23 机械狼规则修正（行动时序 + 猎人技能）

### 问题描述

原实现存在以下三处规则偏差：

1. **行动时序错误**：每晚两阶段并存（学习阶段在狼人前 + 行动阶段在猎人后），与规则"每晚只有一个机械狼阶段，且位于狼人行动之前"不符；同时"机械狼行动请出现/请闭眼"出现在猎人之后，位置错误。
2. **可当晚立即行动**：学习完毕后同一夜即进入行动阶段，实际规则要求**从下一晚起**才能发动习得技能。
3. **学到猎人无实现**：学到猎人时缺少开枪状态展示与出局时的击杀机制；`can_shoot` 标志未在学习时写入，`supports_last_skill()` 永远返回 `False`。

### 修改内容

**`presets/game_config_mechanical_wolf_mirror.py`**

- 将 `handle_custom_pre_wolf_stages()` 改为动态分发：
  - 若 MW 已于**上一晚或更早**学习技能（`learned_night < room.round`），则运行 `MECHANICAL_WOLF_ACT` 阶段（宣告"机械狼行动请出现/请闭眼"）；
  - 否则运行 `MECHANICAL_WOLF_LEARN` 阶段（宣告"机械狼请出现/请闭眼"）。
- 从 `night_role_order()` 中删除 `MECHANICAL_WOLF_ACT` 条目，猎人之后不再有第二个机械狼阶段。
- 新增 `PlayerStatus` 导入（用于判断 MW 是否存活）。

**`roles/mechanical_wolf.py`**

| 位置 | 变更 |
|------|------|
| `name` | `'机械狼'` → `'觉醒隐狼'` |
| `_LEARN_LABELS` | `Role.MAGIC_MIRROR_GIRL` 显示名由"通灵师"改为"魔镜少女" |
| `_in_act_phase()` | 新增守门条件 `learned_night < room.round`，确保当晚学习不能当晚行动 |
| `_in_shoot_mode()` | 新增方法：在 `LAST_WORDS` 阶段且 `pending_last_skill=True` + `can_shoot=True` + `mw_hunter_ready=True` 时返回 True |
| `get_actions()` | 新增首位检查 `_in_shoot_mode()` → 返回开枪选人界面 |
| `input_handlers()` | 新增 `mw_shoot_target` 和 `mw_shoot_confirm` 两个处理器 |
| `confirm()`（学习分支） | 学到猎人时同步写入 `can_shoot = True`；学习成功提示改为"从下一晚起可发动技能" |
| `_get_act_actions()` | 全面重写：所有无可用动作的分支（猎人/狼刀锁定/毒药已用/平民）改为返回"知晓"按钮，不再返回空列表导致 20 秒超时；猎人分支额外推送开枪状态私信（每轮仅一次） |
| `_act_acknowledge()` | 新增辅助方法：推送一次性消息 + 返回"知晓（放弃）"按钮 |
| `_get_shoot_actions()` | 新增：仿猎人 UI，列出存活玩家供选择 + 确认击杀二步操作 |
| `select_shoot_target()` | 新增：选目标或取消开枪 |
| `confirm_shoot()` | 新增：执行击杀，调用 `handle_last_word_skill_kill()` + `advance_last_words_progress()` 推进遗言流程 |

### 机械狼阶段逻辑总结（修正后）

```
每晚开始
  └─ handle_custom_pre_wolf_stages()
       ├─ MW 存活 & 已学技能 & learned_night < 本轮
       │    └─ MECHANICAL_WOLF_ACT（宣告"机械狼行动请出现/请闭眼"）
       │         ├─ 守卫技能 → 选择守护目标
       │         ├─ 魔镜少女技能 → 选择查验目标
       │         ├─ 女巫技能 → 选择毒药目标（一瓶，用完知晓）
       │         ├─ 猎人技能 → 推送开枪状态私信 → 知晓
       │         ├─ 狼人技能（刀可用）→ 选择刀杀目标
       │         └─ 狼人技能（刀未解锁/已用）/ 平民 → 知晓
       └─ 其他情况（首夜/未学/MW已出局）
            └─ MECHANICAL_WOLF_LEARN（宣告"机械狼请出现/请闭眼"）
  └─ 狼人 → 守卫 → 魔镜少女 → 女巫 → 猎人
```

---
