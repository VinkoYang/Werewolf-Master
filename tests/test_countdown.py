import asyncio


class FakeUser:
    def __init__(self):
        self.game_msg = []
        self.main_task_id = 'task-1'


async def countdown(user, seconds=3):
    # 复制 main.py 中的显示样式（使用 3s 做快速测试）
    for i in range(seconds, 0, -1):
        # 模拟 put_html 返回的字符串追加到 game_msg
        user.game_msg.append(f"<div style='color:#c00; font-weight:bold; font-size:18px'>倒计时：{i}s</div>")
        await asyncio.sleep(1)


def test_countdown_format_and_length():
    user = FakeUser()
    asyncio.run(countdown(user, seconds=3))

    # 检查条目数量
    assert len(user.game_msg) == 3

    # 检查格式与样式
    expected = [
        "<div style='color:#c00; font-weight:bold; font-size:18px'>倒计时：3s</div>",
        "<div style='color:#c00; font-weight:bold; font-size:18px'>倒计时：2s</div>",
        "<div style='color:#c00; font-weight:bold; font-size:18px'>倒计时：1s</div>",
    ]
    assert user.game_msg == expected
