# main.py
import asyncio
import sys
import platform
import signal
from logging import getLogger, basicConfig

from pywebio import start_server
from pywebio.input import *
from pywebio.output import *
from pywebio.session import defer_call, get_current_task_id

from enums import WitchRule, GuardRule, Role, GameStage
from models.room import Room
from models.user import User
from utils import add_cancel_button, get_interface_ip

# ==================== 接入外网：pyngrok ====================
from pyngrok import ngrok
import threading

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
        room_config = await input_group('房间设置', inputs=[
            input(name='wolf_num', label='普通狼数', type=NUMBER, value='3'),
            checkbox(name='god_wolf', label='特殊狼', inline=True, options=Role.as_god_wolf_options()),
            input(name='citizen_num', label='普通村民数', type=NUMBER, value='4'),
            checkbox(name='god_citizen', label='特殊村民', inline=True, options=Role.as_god_citizen_options()),
            select(name='witch_rule', label='女巫解药规则', options=WitchRule.as_options()),
            select(name='guard_rule', label='守卫规则', options=GuardRule.as_options()),
        ])
        room = Room.alloc(room_config)
    elif data['cmd'] == '加入房间':
        room = Room.get(await input('房间号', type=TEXT, validate=Room.validate_room_join))
    else:
        raise NotImplementedError

    put_scrollable(current_user.game_msg, height=200, keep_bottom=True)
    current_user.game_msg.append(put_text(room.desc()))

    room.add_player(current_user)

    while True:
        await asyncio.sleep(0.2)

        # 非夜晚房主操作
        host_ops = []
        if current_user is room.get_host():
            if not room.started:
                host_ops = [
                    actions(name='host_op', buttons=['开始游戏'], help_text='你是房主')
                ]
            elif room.stage == GameStage.Day and room.round > 0:
                host_ops = [
                    actions(
                        name='host_vote_op',
                        buttons=[user.nick for user in room.list_alive_players()],
                        help_text='你是房主，本轮需要选择出局玩家'
                    )
                ]

        # === 房主专属关闭服务器按钮 ===
        if current_user is room.get_host():
            host_ops.append(
                actions(
                    name='shutdown_server',
                    buttons=['[房主] 结束游戏并关闭服务器'],
                    help_text='点击后所有玩家断开，服务器关闭'
                )
            )

        # 玩家操作
        user_ops = []
        if room.started:
            if room.stage == GameStage.WOLF and current_user.should_act():
                user_ops = [
                    actions(
                        name='wolf_team_op',
                        buttons=add_cancel_button([user.nick for user in room.list_alive_players()]),
                        help_text='狼人阵营，请选择要击杀的对象。'
                    )
                ]
            if room.stage == GameStage.DETECTIVE and current_user.should_act():
                user_ops = [
                    actions(
                        name='detective_team_op',
                        buttons=[user.nick for user in room.list_alive_players()],
                        help_text='预言家，请选择要查验的对象。'
                    )
                ]
            if room.stage == GameStage.WITCH and current_user.should_act():
                if current_user.witch_has_heal():
                    current_user.send_msg(f'昨晚被杀的是 {room.list_pending_kill_players()}')
                else:
                    current_user.send_msg('你已经没有解药了')

                user_ops = [
                    radio(name='witch_mode', options=['解药', '毒药'], required=True, inline=True),
                    actions(
                        name='witch_team_op',
                        buttons=add_cancel_button([user.nick for user in room.list_alive_players()]),
                        help_text='女巫，请选择你的操作。'
                    )
                ]
            if room.stage == GameStage.GUARD and current_user.should_act():
                user_ops = [
                    actions(
                        name='guard_team_op',
                        buttons=add_cancel_button([user.nick for user in room.list_alive_players()]),
                        help_text='守卫，请选择你的操作。'
                    )
                ]
            if room.stage == GameStage.HUNTER and current_user.should_act():
                current_user.hunter_gun_status()

        ops = host_ops + user_ops
        if not ops:
            continue

        if host_ops + user_ops:
            current_user.input_blocking = True
        data = await input_group('操作', inputs=host_ops + user_ops, cancelable=True)
        current_user.input_blocking = False

        if data is None:
            current_user.skip()
            continue

        # Host logic
        if data.get('host_op') == '开始游戏':
            await room.start_game()
        if data.get('host_vote_op'):
            await room.vote_kill(data.get('host_vote_op'))
        if data.get('wolf_team_op'):
            current_user.wolf_kill_player(nick=data.get('wolf_team_op'))
        if data.get('detective_team_op'):
            current_user.detective_identify_player(nick=data.get('detective_team_op'))
        if data.get('witch_team_op'):
            if data.get('witch_mode') == '解药':
                current_user.witch_heal_player(nick=data.get('witch_team_op'))
            elif data.get('witch_mode') == '毒药':
                current_user.witch_kill_player(nick=data.get('witch_team_op'))
        if data.get('guard_team_op'):
            current_user.guard_protect_player(nick=data.get('guard_team_op'))
        
#        # === 处理关闭服务器 ===
#        if data.get('shutdown_server'):
#            put_markdown("## 服务器正在关闭...")
#            await asyncio.sleep(1)
#            import tornado.ioloop
#            ioloop = tornado.ioloop.IOLoop.current()
#            ioloop.add_callback(ioloop.stop)
#            logger.info("服务器已由房主关闭")
#            return


# ==================== 启动入口（Mac 优化 + pyngrok） ====================
if __name__ == '__main__':
    # 兼容 Windows 事件循环
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # 手动创建事件循环（Python 3.14+ 推荐）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 允许 Ctrl+C 退出
    def stop_server(signum, frame):
        logger.info("正在关闭服务器...")
        import tornado.ioloop
        tornado.ioloop.IOLoop.current().add_callback(
            tornado.ioloop.IOLoop.current().stop
        )
    signal.signal(signal.SIGINT, stop_server)

    port = 8080
    ip = get_interface_ip()

    # ==================== 启动 pyngrok 隧道 ====================
    try:
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
        print(f"ngrok 启动失败（可能是网络问题）：{e}")
        print(f"仅限局域网：http://{ip}:{port}")
        ngrok_url = None

    logger.info(f"狼人杀服务器启动成功！")
    logger.info(f"局域网访问：http://{ip}:{port}")
    if ngrok_url:
        logger.info(f"外网访问：{ngrok_url}")

    # 启动 PyWebIO 服务
    start_server(
        main,
        debug=False,
        host='0.0.0.0',
        port=port,
        cdn=False,
        auto_open_webbrowser=False,
        websocket_ping_interval=25,   # 防止云端断线
        allowed_origins=["*"],        # 允许 ngrok 跨域
    )
