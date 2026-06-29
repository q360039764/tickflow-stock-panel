"""桌面客户端入口 — uvicorn 后台服务 + pywebview 桌面窗口。

运行方式:
  开发模式: python -m app.desktop  (需 pip install pywebview)
  打包后:   双击可执行文件即可

职责:
  1. 完成桌面版启动准备 — 数据目录、静态资源、种子数据、默认偏好
  2. 单实例锁 — 已运行则退出
  3. 选可用端口 — 从 settings.port 起, 被占则递增
  4. 后台线程起 uvicorn (仅监听 127.0.0.1, 不暴露外网)
  5. 主线程起 pywebview 窗口渲染前端
  6. 窗口关闭 → 进程退出
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_NAME = "TickFlow 股票面板"
_BASE_PORT = 3018
_PORT_PROBE_RANGE = 50  # 从 3018 起最多试 50 个端口
_SERVER_READY_TIMEOUT = 240.0  # PyInstaller 首次冷启动导入原生库可能较慢。


def _configure_logging() -> None:
    """配置桌面版日志，同时写入 data/logs/desktop.log。"""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        from app.config import settings

        log_dir = Path(settings.data_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "desktop.log", encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _prepare_startup() -> None:
    """执行桌面版启动前准备。"""
    # 桌面模式标记供后续配置、日志和兼容逻辑判断。
    os.environ.setdefault("TICKFLOW_DESKTOP", "1")

    from app.desktop_bootstrap import prepare_desktop_environment

    prepare_desktop_environment()


def _acquire_single_instance() -> bool:
    """单实例锁。已运行返回 False (本进程应退出), 否则 True。

    用 data_dir/.desktop.lock 文件锁实现。跨进程, 文件存在即视为已运行
    (简单可靠; 不引入 msvcrt/fcntl 平台差异)。
    """
    from app.config import settings

    lock_path = settings.data_dir / ".desktop.lock"
    if lock_path.exists():
        # 软检测: 写入进程 PID, 若该 PID 已不存在则视为残留锁, 允许接管。
        try:
            pid_str = lock_path.read_text(encoding="utf-8").strip()
            pid = int(pid_str) if pid_str.isdigit() else None
        except Exception:  # noqa: BLE001
            pid = None

        if pid is not None and _pid_alive(pid):
            logger.warning("检测到已有实例运行 (PID %d), 本进程退出", pid)
            return False
        logger.info("清理残留单实例锁 (PID %s 已不存在)", pid)

    lock_path.write_text(str(_current_pid()), encoding="utf-8")
    return True


def _release_single_instance() -> None:
    """释放桌面版单实例锁。"""
    from app.config import settings

    lock_path = settings.data_dir / ".desktop.lock"
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _pid_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否存活。"""
    if os.name == "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _current_pid() -> int:
    """返回当前进程 PID。"""
    return os.getpid()


def _find_free_port(start: int, count: int = _PORT_PROBE_RANGE) -> int:
    """从 start 起找第一个可用端口。全部被占则返回 start (交给 uvicorn 报错)。"""
    for port in range(start, start + count):
        if _port_accepts_connection(port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _port_accepts_connection(port: int) -> bool:
    """检查本机端口是否已有服务响应。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _run_uvicorn(port: int, ready_event: threading.Event) -> None:
    """后台线程: 启动 uvicorn 服务。ready_event 在线程退出时置位。"""
    logger.info("开始导入 uvicorn 与 FastAPI 应用")
    import uvicorn

    # 延迟 import app, 确保桌面启动准备已经完成。
    from app.main import app

    logger.info("uvicorn 线程开始启动: 127.0.0.1:%d", port)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",  # 仅本机, 不暴露外网。
        port=port,
        log_level="info",
        access_log=False,
        loop="auto",
        http="h11",
        log_config=None,  # 保留 desktop.log 文件日志，便于排查打包后启动阶段。
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except Exception:  # noqa: BLE001
        logger.exception("uvicorn 线程异常退出")
    finally:
        logger.info("uvicorn 线程结束")
        ready_event.set()


def _wait_for_server(port: int, timeout: float = 60.0) -> bool:
    """轮询 health 接口直到后端就绪或超时。"""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    logger.info("等待后端就绪: %s", url)
    deadline = time.monotonic() + timeout
    last_error = "尚未连接"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    logger.info("后端已就绪: %s", url)
                    return True
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    logger.error("等待后端就绪超时: %s, 最近错误: %s", url, last_error)
    return False


def _open_window(url: str) -> None:
    """主线程: 用 pywebview 打开桌面窗口。"""
    import webview  # type: ignore[import-not-found]

    logger.info("创建 pywebview 窗口: %s", url)

    webview.create_window(
        _APP_NAME,
        url,
        width=1440,
        height=900,
        min_size=(1024, 700),
        confirm_close=False,
    )
    webview.start(debug=False)


def main() -> int:
    """桌面客户端主入口。返回进程退出码。"""

    try:
        _prepare_startup()
        _configure_logging()
        logger.info("桌面版启动准备完成")
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=logging.INFO, force=True)
        logger.exception("桌面版启动准备失败")
        return 1

    if not _acquire_single_instance():
        return 0

    try:
        port = _find_free_port(_BASE_PORT)
        logger.info("桌面版后端将监听 127.0.0.1:%d", port)

        ready = threading.Event()
        server_thread = threading.Thread(
            target=_run_uvicorn,
            args=(port, ready),
            daemon=True,
            name="uvicorn",
        )
        server_thread.start()

        if not _wait_for_server(port, timeout=_SERVER_READY_TIMEOUT):
            logger.error("后端启动超时, 桌面版退出")
            return 1

        url = f"http://127.0.0.1:{port}"
        logger.info("打开桌面窗口: %s", url)
        _open_window(url)

        logger.info("窗口已关闭, 桌面版退出")
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        _release_single_instance()


if __name__ == "__main__":
    sys.exit(main())
