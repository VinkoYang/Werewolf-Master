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
from pywebio.session import defer_call, get_current_task_id


from enums import WitchRule, GuardRule, Role, GameStage, PlayerStatus
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

    put_scrollable(current_user.game_msg, height=200, keep_bottom=True)
    current_user.game_msg.append(put_text(room.desc()))

    room.add_player(current_user)

    while True:
        await asyncio.sleep(0.2)

        # 非夜晚房主操作
        host_ops = []
        if current_user is room.get_host():
            if not room.started:
                host_ops += [
                    actions(name='host_op', buttons=['开始游戏'], help_text='你是房主')
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
            if hasattr(room, 'current_speaker') and room.stage == 'SPEECH' and current_user.nick == room.current_speaker:
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
            current_user.input_blocking = True
            with use_scope('input_group', clear=True):  # 替换 clear('input_group')
                data = await input_group('操作', inputs=ops, cancelable=True)
            current_user.input_blocking = False

        if data is None:
            current_user.skip()
            continue

        # === Host logic ===
        if data.get('host_op') == '开始游戏':
            await room.start_game()
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

    port = 8080
    ip = get_interface_ip()

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
