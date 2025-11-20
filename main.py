# main.py
import asyncio
import sys
import platform
import signal
from logging import getLogger, basicConfig

from pywebio import start_server
from pywebio.input import *
from pywebio.output import *
from pywebio.output import use_scope
from pywebio.session import defer_call, get_current_task_id, get_current_session


from enums import WitchRule, GuardRule, Role, GameStage, PlayerStatus
from models.room import Room
from models.user import User
from utils import add_cancel_button, get_interface_ip

# ==================== 接入外网：pyngrok ====================
from pyngrok import ngrok
import threading
import os

basicConfig(stream=sys.stdout,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = getLogger('Wolf')
logger.setLevel('DEBUG')


async def main():
    put_markdown("## 狼人杀法官")
    current_user = User.alloc(
        await input('请输入你的昵称',
                    required=True,
                    validate=User.validate_nick,
                    help_text='请使用一个易于分辨的名称'),
        get_current_task_id()
    )

    @defer_call
    def on_close():
        User.free(current_user)

    put_text(f'你好，{current_user.nick}')
    data = await input_group(
        '大厅', inputs=[actions(name='cmd', buttons=['创建房间', '加入房间'])]
    )

    if data['cmd'] == '创建房间':
        # 先显示板子预设选择
        preset_data = await input_group('板子预设', inputs=[
            actions(name='preset', buttons=['3人测试板子', '自定义配置'], help_text='选择预设或自定义')
        ])
        
        if preset_data['preset'] == '3人测试板子':
            # 使用3人测试板子预设：1普通狼人，1平民，1预言家
            room_config = {
                'wolf_num': 1,
                'god_wolf': [],
                'citizen_num': 1,
                'god_citizen': ['预言家'],
                'witch_rule': '仅第一夜可自救',
                'guard_rule': '同时被守被救时，对象死亡'
            }
        else:
            # 自定义配置
            room_config = await input_group('房间设置', inputs=[
                input(name='wolf_num', label='普通狼数', type=NUMBER, value='3'),
                checkbox(name='god_wolf', label='特殊狼', inline=True, options=Role.as_god_wolf_options()),
                input(name='citizen_num', label='普通村民数', type=NUMBER, value='4'),
                checkbox(name='god_citizen', label='特殊村民', inline=True,
                         options=Role.as_god_citizen_options()),
                select(name='witch_rule', label='女巫解药规则', options=WitchRule.as_options()),
                select(name='guard_rule', label='守卫规则', options=GuardRule.as_options()),
            ])
        room = Room.alloc(room_config)
    elif data['cmd'] == '加入房间':
        room = Room.get(await input('房间号', type=TEXT, validate=Room.validate_room_join))
    else:
        raise NotImplementedError

    # 增大消息显示区域高度，提供更充足的聊天/系统信息显示空间
    put_scrollable(current_user.game_msg, height=400, keep_bottom=True)
    current_user.game_msg.append(put_text(room.desc()))

    room.add_player(current_user)

    while True:
        await asyncio.sleep(0.2)

        # 非夜晚房主操作
        host_ops = []
        if current_user is room.get_host():
            if not room.started:
                host_ops += [
                    actions(name='host_op', buttons=['开始游戏', '房间配置'], help_text='你是房主')
                ]
            elif room.stage == GameStage.Day and room.round > 0:
                host_ops += [
                    actions(
                        name='host_vote_op',
                        buttons=[f"{user.seat}. {user.nick}" for user in room.list_alive_players()],  # 添加座位号
                        help_text='你是房主，本轮需要选择出局玩家'
                    )
                ]

        # 玩家操作
        user_ops = []
        if room.started and current_user.role_instance:
            user_ops = current_user.role_instance.get_actions()

            # === 上警阶段：10秒举手 ===
            if room.stage == GameStage.SHERIFF and current_user.status == PlayerStatus.ALIVE:
                user_ops += [
                    actions(
                        name='sheriff_vote',
                        buttons=['上警', '不上警'],
                        help_text='请选择是否上警（10秒内，未选视为不上警）'
                    )
                ]

            # === 发言阶段 ===
            if hasattr(room, 'current_speaker') and room.stage == GameStage.SPEECH and current_user.nick == room.current_speaker:
                user_ops += [
                    put_text('你的发言时间到！'),
                    actions(
                        name='speech_done',
                        buttons=['发言完毕'],
                        help_text='点击结束发言'
                    )
                ]

        # === 房主专属：公布昨夜死亡 ===
        if current_user is room.get_host() and hasattr(room, 'death_pending') and room.death_pending:
            host_ops += [
                actions(
                    name='publish_death',
                    buttons=['公布昨夜信息'],
                    help_text='点击公布昨夜出局玩家'
                )
            ]

        ops = host_ops + user_ops
        if not ops:
            continue

        if ops:
            # 夜间操作显示 20s 倒计时与确认键
            if room.stage is not None:
                # 仅在有玩家操作时（夜晚阶段）追加确认键
                # 避免重复添加：只在 user_ops 非空且为夜间角色时加入确认
                try:
                    if current_user.role_instance and current_user.role_instance.can_act_at_night:
                        ops = ops + [actions(name='confirm_action', buttons=['确认'], help_text='确认当前选择（20秒内）')]
                except Exception:
                    pass

            # 开启倒计时任务（每个玩家单独）仅在夜间角色可行动时启动
            NIGHT_STAGES = {GameStage.WOLF, GameStage.SEER, GameStage.WITCH, GameStage.GUARD, GameStage.HUNTER, GameStage.DREAMER}
            async def _countdown(user, seconds=20):
                try:
                    for i in range(seconds, 0, -1):
                        # 调试日志（不再发送到玩家私聊或终端），仅在 logger 中记录
                        # 不在终端或私聊输出调试信息，避免污染日志/消息区

                        # 在操作窗口内的专用 scope 中更新倒计时（覆盖同一行），避免消息区污染
                        try:
                            with use_scope(f'input_countdown_{user.nick}', clear=True):
                                put_html(f"<div style='color:#c00; font-weight:bold; font-size:18px'>倒计时：{i}s</div>")
                        except Exception:
                            # 忽略更新失败
                            pass

                        await asyncio.sleep(1)

                    try:
                        # 超时时，若玩家已做出临时选择则确认之；否则视为放弃并跳过
                        pending_keys = [
                            'wolf_choice', 'pending_witch_action', 'pending_protect',
                            'pending_dream_target', 'pending_target'
                        ]
                        has_pending = any(user.skill.get(k) for k in pending_keys)

                        if has_pending and user.role_instance and hasattr(user.role_instance, 'confirm'):
                            try:
                                user.role_instance.confirm()
                            except Exception:
                                pass
                        else:
                            # 没有选择 -> 跳过当前玩家动作
                            try:
                                user.skip()
                            except Exception:
                                pass

                        # 无论如何都发送客户端取消事件以收起输入控件
                        try:
                            get_current_session().send_client_event({'event': 'from_cancel', 'task_id': user.main_task_id, 'data': None})
                        except Exception:
                            pass
                    except Exception:
                        pass
                finally:
                    user.skill.pop('countdown_task', None)

                    # 清理倒计时显示（操作窗口内）
                    try:
                        with use_scope(f'input_countdown_{user.nick}', clear=True):
                            put_html('')
                    except Exception:
                        pass
            # 仅当处于夜间阶段且当前玩家为能在夜间行动的角色时才启动倒计时
            try:
                is_night_stage = room.stage in NIGHT_STAGES
            except Exception:
                is_night_stage = False

            if current_user.skill.get('countdown_task') is None and is_night_stage:
                try:
                    if current_user.role_instance and current_user.role_instance.can_act_at_night:
                        # 清理房间日志中遗留的倒计时私聊信息，避免旧条目继续显示在 Private 区
                        try:
                            if current_user.room and isinstance(current_user.room.log, list):
                                filtered = [e for e in current_user.room.log if not (e[0] == current_user.nick and isinstance(e[1], str) and '倒计时' in e[1])]
                                current_user.room.log = filtered
                        except Exception:
                            pass

                        task = asyncio.create_task(_countdown(current_user, 20))
                        current_user.skill['countdown_task'] = task
                except Exception:
                    pass

            current_user.input_blocking = True
            with use_scope('input_group', clear=True):  # 替换 clear('input_group')
                # 在操作窗口内创建单行倒计时显示 scope（仅在夜间阶段且玩家可行动时）
                try:
                    if is_night_stage and current_user.role_instance and current_user.role_instance.can_act_at_night:
                        # 在 input_group scope 内创建一个可更新的子 scope 占位符，保证其显示在操作窗口内
                        try:
                            put_scope(f'input_countdown_{current_user.nick}')
                        except Exception:
                            pass
                except Exception:
                    pass

                data = await input_group('操作', inputs=ops, cancelable=True)
            current_user.input_blocking = False

            # 如果用户按下确认键，取消倒计时并调用角色确认方法（若存在）
            if data and data.get('confirm_action'):
                task = current_user.skill.pop('countdown_task', None)
                if task:
                    task.cancel()
                # 清理倒计时显示（操作窗口内）
                try:
                    with use_scope(f'input_countdown_{current_user.nick}', clear=True):
                        put_html('')
                except Exception:
                    pass
                # 调用角色 confirm（若实现）
                if current_user.role_instance and hasattr(current_user.role_instance, 'confirm'):
                    try:
                        rv = current_user.role_instance.confirm()
                    except Exception as e:
                        current_user.send_msg(f'确认失败: {e}')
                # 跳过后续动作处理（confirm 已处理）
                await asyncio.sleep(0.1)
                continue

        if data is None:
            # 清理倒计时显示并跳过
            try:
                with use_scope(f'input_countdown_{current_user.nick}', clear=True):
                    put_html('')
            except Exception:
                pass
            current_user.skip()
            continue

        # === Host logic ===
        if data.get('host_op') == '开始游戏':
            await room.start_game()
        if data.get('host_op') == '房间配置':
            # 房主重新配置房间
            room_config = await input_group('房间设置', inputs=[
                input(name='wolf_num', label='普通狼数', type=NUMBER, value=str(room.roles.count(Role.WOLF))),
                checkbox(name='god_wolf', label='特殊狼', inline=True, options=Role.as_god_wolf_options(),
                        value=[opt for opt in Role.as_god_wolf_options() if Role.from_option(opt) in room.roles]),
                input(name='citizen_num', label='普通村民数', type=NUMBER, value=str(room.roles.count(Role.CITIZEN))),
                checkbox(name='god_citizen', label='特殊村民', inline=True, options=Role.as_god_citizen_options(),
                        value=[opt for opt in Role.as_god_citizen_options() if Role.from_option(opt) in room.roles]),
                select(name='witch_rule', label='女巫解药规则', options=WitchRule.as_options(),
                      value=list(WitchRule.mapping().keys())[list(WitchRule.mapping().values()).index(room.witch_rule)]),
                select(name='guard_rule', label='守卫规则', options=GuardRule.as_options(),
                      value=list(GuardRule.mapping().keys())[list(GuardRule.mapping().values()).index(room.guard_rule)]),
            ])
            # 更新房间配置
            from copy import copy
            roles = []
            roles.extend([Role.WOLF] * room_config['wolf_num'])
            roles.extend([Role.CITIZEN] * room_config['citizen_num'])
            roles.extend(Role.from_option(room_config['god_wolf']))
            roles.extend(Role.from_option(room_config['god_citizen']))
            room.roles = copy(roles)
            room.roles_pool = copy(roles)
            room.witch_rule = WitchRule.from_option(room_config['witch_rule'])
            room.guard_rule = GuardRule.from_option(room_config['guard_rule'])
            room.broadcast_msg(f'房间配置已更新：{room.desc()}')
        if data.get('host_vote_op'):
            voted_nick = data.get('host_vote_op').split('.')[-1].strip()
            await room.vote_kill(voted_nick)
            voted_out = room.players.get(voted_nick)  # 修改为 voted_nick
            if voted_out and voted_out.role == Role.HUNTER and voted_out.skill.get('can_shoot', False):
                voted_out.send_msg('🔫 你是猎人，可以立即开枪！')
                # 这里可以添加猎人开枪按钮逻辑

        # === 夜晚行动处理（调用 role_instance） ===
        if data.get('wolf_team_op'):
            current_user.role_instance.kill_player(data.get('wolf_team_op'))
        if data.get('seer_team_op'):
            current_user.role_instance.identify_player(data.get('seer_team_op'))
        if data.get('witch_team_op'):
            mode = data.get('witch_mode')
            if mode == '解药':
                current_user.role_instance.heal_player(data.get('witch_team_op'))
            elif mode == '毒药':
                current_user.role_instance.kill_player(data.get('witch_team_op'))
        if data.get('guard_team_op'):
            current_user.role_instance.protect_player(data.get('guard_team_op'))
        if data.get('dreamer_team_op'):
            current_user.role_instance.select_target(data.get('dreamer_team_op'))
        if data.get('hunter_confirm'):
            current_user.skip()

        # === 上警与发言 ===
        if data.get('sheriff_vote'):
            current_user.skill['sheriff_vote'] = data.get('sheriff_vote')
            current_user.skip()

        if data.get('speech_done') and current_user.nick == room.current_speaker:
            current_user.skip()

        # === 公布死亡 ===
        if data.get('publish_death') and current_user is room.get_host():
            death_list = room.death_pending
            death_msg = "无人" if not death_list else "，".join(death_list)
            room.broadcast_msg(f'昨夜 {death_msg} 出局', tts=True)
            room.death_pending = []  # 清空
            room.stage = GameStage.Day
            room.broadcast_msg('现在开始投票')

        # 防止按钮闪烁
        await asyncio.sleep(0.3)


# ==================== 启动入口（Mac 优化 + pyngrok） ====================
if __name__ == '__main__':
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def stop_server(signum, frame):
        logger.info("正在关闭服务器...")
        import tornado.ioloop
        tornado.ioloop.IOLoop.current().add_callback(
            tornado.ioloop.IOLoop.current().stop
        )
    signal.signal(signal.SIGINT, stop_server)

    # 默认端口，可通过环境变量 `PORT` 覆盖（方便在端口被占用时切换）
    port = int(os.environ.get('PORT', '8080'))
    ip = get_interface_ip()

    ngrok_url = None
    if os.environ.get('DISABLE_NGROK', '').lower() in ('1', 'true', 'yes'):
        print("已检测到 DISABLE_NGROK，跳过 ngrok 连接，服务仅在局域网可见。")
    else:
        try:
            # 如果没有提供 authtoken，则跳过 ngrok（避免频繁出现认证错误日志）
            if not os.environ.get('NGROK_AUTHTOKEN') and not os.environ.get('NGROK_AUTH_TOKEN'):
                raise RuntimeError('未提供 NGROK_AUTHTOKEN，跳过 ngrok 连接')

            public_url = ngrok.connect(port, bind_tls=True)
            ngrok_url = str(public_url).replace("NgrokTunnel: \"", "").replace("\"", "")
            print("\n" + "="*70)
            print("       狼人杀已上线！全球可玩！")
            print(f"       局域网地址 → http://{ip}:{port}")
            print(f"       公网地址 → {ngrok_url}")
            print("       分享这个链接给所有玩家：")
            print(f"       {ngrok_url}")
            print("="*70 + "\n")
        except Exception as e:
            print(f"ngrok 启动失败（可能是网络或未授权）：{e}")
            print(f"仅限局域网：http://{ip}:{port}")
            ngrok_url = None

    logger.info(f"狼人杀服务器启动成功！")
    logger.info(f"局域网访问：http://{ip}:{port}")
    if ngrok_url:
        logger.info(f"外网访问：{ngrok_url}")

    start_server(
        main,
        debug=False,
        host='0.0.0.0',
        port=port,
        cdn=False,
        auto_open_webbrowser=False,
        websocket_ping_interval=25,
        allowed_origins=["*"],
    )
