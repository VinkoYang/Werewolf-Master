# Wolf
狼人杀面杀法官系统

Preview
--
![房间设置界面](doc/room_setting.png)

如何使用
--
0. 安装 Python 3.7 版本及以上
1. pip install -r requirements.txt
2. python main.py
3. 所有玩家访问 Web 服务

--
使用虚拟环境：
```{
spython3 -m venv venv
source venv/bin/activate
pip install "pywebio==1.8.3"
python main.py
}
```
## 身份介绍
--
1. TTS 目前仅支持 macOS，windows，需要支持更多的平台
2. 多平台的 Standalone executable
3. 未对断线重连做支持 (等待 PyWebIO 支持)
4. 狼人自爆操作
   1. 在日间自杀，直接进入夜晚
5. 狼王技能
    1. 被猎人枪杀/日间投票出局可以带走一个人
    2. 被女巫毒害无法带人
6. 猎人技能
    1. 被狼人杀害/日间投票出局可以带走一个人
    2. 被女巫毒害无法带人
7. 赤月使徒
技能：赤月使徒在自曝身份后将会直接进入黑夜，当晚所有好人的技能都将会被封印。若血月使徒是最后一个被放逐出局的狼人，他可以存活到下一个白天天亮之后才出局
8.摄梦人
    好人阵营，神职。每晚必须选择一名除自己以外的玩家成为梦游者（未主动选择则系统随机选择），梦游者不知道自己正在梦游，且免疫一切夜间伤害。连续两晚选择同一名玩家会致其死亡。若摄梦人在夜晚死亡，则梦游者一并死亡。
    *因梦游出局的猎人、狼王均不能开枪。
    
## 待解决bug
- 预言家验人问题
- 猎人闭眼之后不动
- 守卫盾人信息不给奶穿


--
## 程序说明
这个程序是一个基于PyWebIO的狼人杀在线游戏系统，包含多个Python脚本，每个脚本负责特定功能
### 1. 整体程序架构
这是一个异步Web应用，使用PyWebIO处理用户输入/输出，asyncio管理并发。核心是多人游戏房间（Room），每个房间管理玩家（User）和游戏状态。程序模拟狼人杀规则，包括角色（狼人、神职等）、夜晚行动（刀人、查验等）和白天投票。

enums.py：定义常量和枚举（如角色Role、游戏阶段GameStage）。这是基础层，被其他脚本导入使用，不直接执行逻辑。
system.py：管理全局状态（如用户和房间的字典）。它是数据存储层，支持房间/用户的注册和获取。
user.py：定义玩家类（User），处理玩家个人操作（如狼人刀人、女巫用药）和消息同步。User实例依赖Room实例。
room.py：定义房间类（Room），核心游戏逻辑在这里，包括角色分配、夜晚循环、死亡结算。Room实例管理多个User实例。
main.py：程序入口，处理用户交互（如创建/加入房间、按钮输入）。它整合所有模块，启动服务器和游戏循环。

程序的层次：enums（常量） → system（全局管理） → user/room（核心模型） → main（交互入口）。

### 2. 脚本间关系
#### enums.py：被几乎所有脚本导入，提供枚举定义。
main.py 导入：用于房间配置（如WitchRule）和游戏阶段检查。
room.py 导入：用于角色池（roles_pool）、阶段（stage）和状态（status）。
user.py 导入：用于玩家角色（role）和技能（skill）。
system.py 不直接导入enums（但间接通过room/user使用）。

#### system.py：定义Global类（管理users和rooms字典）和Config（系统昵称）。
room.py 导入：使用Global.reg_room()注册房间、Global.get_room()获取房间。
user.py 导入：使用Global.users注册/注销用户。
main.py 导入：但不直接使用Global（通过Room/User间接）。

#### user.py：定义User类。
导入 enums.py（角色/状态）、system.py（Global/Config）。
与 room.py 相互引用：使用TYPE_CHECKING避免循环导入。User有room属性，Room有players字典（key: nick, value: User）。
main.py 导入：创建User实例，调用User方法处理输入。

#### room.py：定义Room类。
导入 enums.py（所有枚举）、system.py（Global）、user.py（User）、utils.py（rand_int, say）。
与 user.py 相互引用：Room管理User列表，调用User方法（如should_act()）。
main.py 导入：创建/获取Room，调用Room方法（如start_game()）。

#### main.py：入口脚本。
导入 enums.py（配置选项）、room.py（Room）、user.py（User）、utils.py（add_cancel_button, get_interface_ip）、system.py（不直接，但通过Room/User）。
整合一切：创建User/Room，处理PyWebIO输入，调用Room/User的逻辑方法。


数据流动和关系图

全局管理（system.py）：
Global.rooms: dict[str, Room]（房间ID → Room实例）。
Global.users: dict[str, User]（昵称 → User实例）。

房间与玩家（room.py 和 user.py）：
Room.players: dict[str, User]（昵称 → User）。
User.room: Optional[Room]（玩家所属房间）。
Room.log: List[Tuple[Union[str, None], Union[str, LogCtrl]]]（消息日志，User通过_syncer同步到game_msg）。

枚举（enums.py）：提供常量，如Role.WOLF用于User.role和Room.roles_pool。
入口（main.py）：用户输入 → 创建User/Room → Room.add_player(User) → 游戏循环（Room.game_loop()） → 用户行动（User.wolf_kill_player()等）。

### 3. 调用逻辑
程序的执行流程是异步的，使用asyncio和PyWebIO的协程。核心是main()函数的while循环，监听用户输入并调用User/Room方法。
启动流程（main.py）

服务器启动：if name == 'main' → 使用pyngrok暴露端口 → start_server(main, port=8080)。
设置信号处理（SIGINT关闭服务器）。
输出局域网/公网地址。

用户会话（main()协程）：
输入昵称 → User.alloc(nick, task_id)（注册到Global.users）。
defer_call(on_close)：会话关闭时User.free()（注销用户）。
输入大厅选项（创建/加入房间）：
创建房间：输入配置（狼数、神职等） → Room.alloc(room_setting)（使用enums.Role.from_option解析选项，注册到Global.rooms）。
加入房间：输入ID → Room.get(room_id)（从Global.rooms获取） → 验证（validate_room_join）。

put_scrollable(user.game_msg)：显示消息框。
room.add_player(user)：加入房间，启动user.start_syncer()（消息同步线程）。

主循环（while True in main()）：
await asyncio.sleep(0.2)：轮询。
根据条件显示按钮（input_group）：
房主（room.get_host()）：显示“开始游戏”、“公布死亡”、“结束服务器”等。
玩家：根据room.stage显示行动按钮（如狼人阶段：wolf_team_op选择刀人）。

用户点击按钮 → data = await input_group() → 根据data调用方法：
'host_op' == '开始游戏' → room.start_game()。
'wolf_team_op' → user.wolf_kill_player(nick)。
类似：seer_identify_player、witch_kill_player等（这些方法用@player_action装饰，确保阶段匹配）。
'publish_death' → 房主公布死亡（room.broadcast_msg），设置room.stage = GameStage.Day。

输入阻塞（user.input_blocking = True/False）：防止并发输入。


游戏核心逻辑（room.py）

房间创建（Room.alloc）：
解析配置：roles = [Role.WOLF * wolf_num + ...]（使用enums.Role）。
roles_pool = copy(roles)（用于洗牌）。
witch_rule/guard_rule 从enums解析。

启动游戏（room.start_game()）：
检查人数（len(players) >= len(roles)）。
洗牌分配：random.shuffle(roles_pool) → user.role = pop()。
初始化技能（如女巫的heal/poison）。
run_async(room.game_loop())：启动游戏循环线程。

游戏循环（room.game_loop()）：
while True：
if not started: sleep。
await night_logic()：夜晚阶段。
await check_game_end()：检查胜负（狼人/好人全灭 → end_game()）。

end_game()：广播结束，清理角色/线程。

夜晚逻辑（room.night_logic()）：
self.round += 1，广播“天黑请闭眼”。
逐阶段（stage = GameStage.WOLF/SEER/...）：
广播“XX请出现”。
self.waiting = True → await wait_for_player()（超时60s）。
玩家行动（通过main的按钮调用User方法，设置status如PENDING_DEAD）。
广播“XX请闭眼”。

结算：
apply_dreamer_logic()：处理摄梦人（免疫/连续死亡，使用utils.rand_int随机）。
统一结算死亡（PENDING_* → DEAD/ALIVE，根据规则如guard_rule）。
广播“天亮请睁眼”，设置stage = GameStage.Day（或第一天SHERIFF）。

第一天：stage = GameStage.SHERIFF（上警，未完整实现）。

白天逻辑：
房主公布death_pending（main中处理）。
投票（host_vote_op → room.vote_kill(nick)）：设置DEAD，检查猎人开枪（未完整）。

消息和广播（room.py）：
broadcast_msg(text, tts)：log.append((SYS_NICK, text))，utils.say(tts)。
send_msg(text, nick)：log.append((nick, text))。
broadcast_log_ctrl(ctrl)：log.append((None, ctrl))，如RemoveInput取消输入。


玩家逻辑（user.py）

创建/注销：alloc/free，使用Global.users。
消息同步（_game_msg_syncer）：run_async循环，读取room.log → append到game_msg（私聊/公聊区分）。
行动方法（@player_action装饰）：
检查room.waiting和should_act()（根据stage和role匹配enums）。
示例：wolf_kill_player(nick) → target.status = PENDING_DEAD，room.waiting = False（结束等待）。
返回str（错误消息）或True/None（成功）。

技能检查：如witch_has_heal()、hunter_gun_status()。

全局和枚举（system.py 和 enums.py）

system.Global：静态方法如reg_room(room)（分配ID）、get_room(id)。
enums：提供mapping()和from_option()，用于配置解析（如main的input_group → Role.from_option）。

--
#Update
## v0.6:
User类添加seat属性，在add_player时分配序号如len(self.players)+1

-通用玩家脚本：保留 user.py 中的 User 类作为通用玩家实体。它包含玩家的基本属性（如昵称、房间、状态、技能字典等），以及通用方法（如 send_msg、skip）。User 现在持有两个与角色相关的属性：
role: 仍然是 enums.Role 枚举值（用于快速检查角色类型）。
role_instance: 一个具体角色类的实例（继承自基类 RoleBase），负责处理该角色的特定技能和逻辑。

- 各个有技能玩家的单独脚本：引入一个新目录 roles/，其中：
base.py：定义基类 RoleBase，包含角色通用的方法（如 should_act、activate_skill 等）。所有角色类都继承自它。
每个具体角色有一个单独的文件（如 wolf.py、seer.py 等），定义该角色的类（e.g., class Wolf(RoleBase)）。角色特定的技能方法（如狼人的 kill_player、预言家的 identify_player 等）都移到这些类中。
平民（Citizen）和无技能角色也用一个简单类实现（继承基类，但技能方法为空或简单实现）。

- 技能集中管理：所有与角色相关的技能逻辑都移到对应的角色类中。添加/删除角色只需：
在 roles/ 目录下添加/删除文件和类。
在 enums.py 的 Role 枚举中添加/删除值。
在 room.py 的角色分配逻辑中更新角色类映射（role_classes 字典）。

其他调整：
room.py 中的游戏逻辑（如夜晚阶段）现在通过 user.role_instance 调用具体技能。
main.py 中的输入处理逻辑调整为调用 user.role_instance 的方法。
移除了 user.py 中的具体技能方法（如 wolf_kill_player），改为委托给 role_instance。
装饰器 player_action 移到 roles/base.py 中，作为角色方法的装饰器。
摄梦人逻辑（原 apply_dreamer_logic）移到 roles/dreamer.py 的类方法中。
保持原有文件结构，但新增 roles/ 目录。

- 兼容性：重构后，代码功能保持不变，但更模块化。添加新角色（如“白痴”）只需创建 roles/idiot.py，定义类，实现技能，然后在映射中注册。

## 2025-11-20 Moonlight 分支优化

1. 在游戏开始处添加公共隔断（一行 ======================），然后广播 游戏开始！身份发放中...。
在每晚开始处添加夜数隔断，格式为 ============ 第 n 晚 ============，随后广播 天黑请闭眼。

2. 狼人操作改动（文件 & 要点）：

wolf.py

get_actions:
显示房间中所有玩家（包含狼人自己）。
已出局玩家按钮不可点击并显示为灰色（disabled + color='secondary'）。
使用按钮 dict 支持 color/disabled，若某玩家被一个或多个狼选择，按钮显示为红色（danger）。
在按钮上方显示当前被哪些狼人选中（例如："Bob 被 狼A, 狼B 选择"），用红色文本呈现。
kill_player:
接受 "seat. nick" 格式输入并提取昵称。
支持更改选择：如果狼之前选过别的目标，会把之前的选择移除（安全检查后移除）。
把选择记录到 room.skill['wolf_votes']（字典：target -> list of wolf nicks），并把狼自己的 user.skill['wolf_choice'] 记录为当前选择；设置 acted_this_stage=True。
room.py

3. 倒计时改动 
夜间狼人结算由原来的 wolf_kill set 改成 wolf_votes 映射：
统计每个被选目标的票数，选择票数最多的目标作为今晚唯一的被刀对象；若存在平票则随机从平票中选一位。
将被选中的玩家设为 PENDING_DEAD，并在 public log 中写入提示（非语音）。
清理 wolf_votes 数据与玩家的 wolf_choice 临时字段。
通用：
在 main.py 中添加每位有夜间操作的玩家的 20s 倒计时（每人单独任务），倒计时结束会自动取消当前输入（相当于超时跳过）。
夜间操作界面自动追加 确认 按钮；点击则取消倒计时并调用玩家角色对象的 confirm() 方法（若实现），以提交选择并结束等待。
行为支持：
修改 base.py 中 player_action：新增返回值约定，支持 'PENDING'（暂存选择）与 'CONFIRMED'（最终确认）的语义，只有最终确认/True/None 会结束等待。
将夜间角色的选择改为“暂存 + 确认”模式（示例实现已完成）：
wolf.py：选择暂存为 user.skill['wolf_choice']，confirm() 会把选择登记到 room.skill['wolf_votes']。夜间结算会统计票数并选出最多票目标（平票随机）。
seer.py：选择暂存为 pending_target，confirm() 会最终公布查验结果。
guard.py：选择暂存为 pending_protect，confirm() 生效后设置守护。
dreamer.py：选择暂存为 pending_dream_target，confirm() 生效。
witch.py：解药/毒药选择暂存为 pending_witch_action，confirm() 统一处理 heal/kill。
hunter.py：将开枪入口适配为支持确认（占位式实现，开枪目标逻辑可后续完善）。
其他：调整了若干细节（如安全移除先前狼票、清理倒计时任务等）。

3. 板子预设
 创建房间界面的板子预设

在选择"创建房间"后，会先显示"板子预设"选择界面
提供两个按钮：
"3人测试板子"：自动配置为 1普通狼人 + 1平民 + 1预言家
"自定义配置"：进入原有的详细配置界面
选择预设后直接创建房间，无需手动填写各项设置

4. 房主的房间配置功能

游戏开始前，房主操作界面的按钮从原来的"开始游戏"变为两个按钮：
"开始游戏"：启动游戏
"房间配置"：重新调整房间设置
点击"房间配置"后会弹出配置界面，显示当前配置的默认值（可修改）
保存后会更新房间配置并广播通知所有玩家

## 2025-11-21 优化
1. ✅ 夜晚操作窗口 - 按钮变黄色标记待选
    位置：wolf.py 的 get_actions() 方法
    实现：当玩家点击某个玩家按钮后，该按钮变为黄色（warning），标志进入待选状态
    特性：倒计时未结束前可以随时更换选择，黄色标记会跟随更新
2. ✅ 狼人确认后广播消息
    位置：wolf.py 的 confirm() 方法
    实现：当一个狼人玩家点击"确认"键后，所有狼人玩家都会收到私聊消息："X号玩家选择击杀Y号玩家"
    特殊情况：如果选择"放弃"，则广播："X号玩家选择放弃"
3. ✅ 狼人击杀判断逻辑
    位置：room.py 的 night_logic() 方法
    实现规则：
        a. 如果狼人团队只选择了一个玩家，则该玩家就是今夜被击杀的目标
        b. 如果狼人团队选择了多个玩家，得票最多的玩家是今夜被击杀的目标
        c. 如果出现平票（多个玩家得票相同），系统自动从平票玩家中随机选择
        d. 如果所有狼人都没有选择或点击了"放弃"，则今夜空刀

4. ✅ 狼人出现时发送队友信息
    位置：room.py 的 night_logic() 方法
    实现：在"Public: 狼人请出现"之后，先给所有狼人玩家发送私聊信息
    信息格式："狼人玩家是：1号、3号(狼王)、5号"
    特性：如果有狼王，会特别标注
    5. ✅ 击杀判定后发送结果并延迟闭眼
    位置：room.py 的 night_logic() 方法
    实现：系统判定完今夜击杀玩家后，给所有狼人发送私聊消息
    消息格式：
    有击杀："今夜，狼队选择X号玩家被击杀。"
    空刀："今夜，狼队空刀。"
    时序：发送消息后延迟3秒，再显示"Public: 狼人请闭眼"
    
    修改的文件
        wolf.py

            添加了 Role 枚举导入
            修改了 get_actions() 方法，实现按钮黄色标记
            修改了 confirm() 方法，实现确认后的广播功能
        room.py

            在狼人阶段开始时添加狼队成员信息广播
            优化了狼人击杀判断逻辑，支持单选/多选/平票/空刀所有情况
            在击杀判定后发送结果消息给所有狼人
            调整了时序，在发送结果后延迟3秒再闭眼

5. 预言家选择按键点击后按钮没有变黄色
修复范围：所有夜间神职角色
    预言家 (seer.py)
    守卫 (guard.py)
    摄梦人 (dreamer.py)
    女巫 (witch.py)
实现方式：
    在每个角色的 get_actions() 方法中获取 pending_* 临时选择
    当按钮对应的玩家是当前临时选择时，设置 'color': 'warning' 使按钮变黄
    玩家可以在确认前随时更换选择，黄色标记会实时更新

6. 未行动的狼人没收到击杀结果私聊消息 ✅
修复文件：room.py

问题原因：
原代码在发送击杀结果消息时有条件 u.status == PlayerStatus.ALIVE，这会排除已死亡但仍是狼人的玩家。

修复方案：
移除了状态检查条件，改为只要是狼人角色（Role.WOLF 或 Role.WOLF_KING）就发送消息，无论玩家是否存活或是否已行动。