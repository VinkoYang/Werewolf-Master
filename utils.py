# utils.py
import asyncio
import random
import re
import socket
import subprocess
import threading
import traceback
from logging import getLogger
from sys import platform

try:
    from pywebio.session import run_asyncio_coroutine
    from pywebio.exceptions import SessionClosedException, SessionNotFoundException
except ImportError:  # pragma: no cover - PyWebIO is required in runtime, but keep graceful fallback
    run_asyncio_coroutine = None
    SessionClosedException = RuntimeError
    SessionNotFoundException = RuntimeError

logger = getLogger('Utils')
logger.setLevel('DEBUG')


async def async_sleep(seconds: float):
    """兼容 PyWebIO 环境的异步 sleep 函数"""
    if seconds <= 0:
        return

    if run_asyncio_coroutine is not None:
        coro = asyncio.sleep(seconds)
        try:
            await run_asyncio_coroutine(coro)
            return
        except (RuntimeError, SessionClosedException, SessionNotFoundException):
            # 当前上下文不是 PyWebIO（或会话已关闭），需要关闭当前协程以避免警告
            try:
                coro.close()
            except Exception:
                pass
            # 回退至默认 asyncio
            pass

    await asyncio.sleep(seconds)

def rand_int(min_value=0, max_value=100):
    return random.randint(min_value, max_value)

def say(text: str):
    if not text:
        return
    def _mac_say():
        try:
            subprocess.Popen(['say', '-r', '180', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning(f"macOS say 失败: {e}")
    def _windows_tts():
        try:
            import pyttsx3
            tts = pyttsx3.init()
            tts.setProperty('rate', 180)
            tts.say(text)
            tts.runAndWait()
        except ImportError:
            logger.warning("pyttsx3 未安装")
        except Exception as e:
            logger.warning(f"Windows TTS 失败: {e}")
    if platform == "darwin":
        threading.Thread(target=_mac_say, daemon=True).start()
    elif platform == "win32":
        threading.Thread(target=_windows_tts, daemon=True).start()
    else:
        logger.info(f"[语音] {text}")

def get_interface_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            logger.warning("获取 IP 失败")
            return "127.0.0.1"

def add_cancel_button(buttons: list):
    if not isinstance(buttons, list):
        buttons = []
    return buttons + [{'label': '放弃', 'type': 'cancel'}]


def make_scope_name(prefix: str, nick: str) -> str:
    """Generate a PyWebIO scope-safe name for a user."""
    suffix = re.sub(r'[^0-9A-Za-z_-]', '_', nick or '')
    if not suffix:
        suffix = 'player'
    return f'{prefix}_{suffix}'
