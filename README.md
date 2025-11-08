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
Update
- User类添加seat属性，在add_player时分配序号如len(self.players)+1


    


