"""Flask微内核入口：UI 主进程 + 守护子进程 + Flask 子进程。

进程树（方案 2.1）：

    python main.py            # 默认：守护进程 + Flask + PySide6 控制面板
    python main.py --web 8080 # 只跑 Flask（开发/调试用，无守护进程）
    python main.py --guardian 8080  # 只跑守护进程（服务器 / 无界面环境）
    python main.py --ui 8080  # 只跑 PySide6 控制面板（服务需已在运行）

启动前会先做**端口可用性预检**（独占 bind）。
为什么必须预检：Windows 的 SO_REUSEADDR 语义会让 Flask 在端口已被占用时
"绑定成功"并打印启动日志，但浏览器请求实际被另一个进程接走，表现为
"网页打不开"。预检发现问题时：日志里写明占用进程，并按
PORT_FALLBACKS 自动换备用端口，保证网页一定起得来。

退出时必须把 app 子进程一并清掉，否则它会继续占用端口，导致下次启动
新代码绑不上端口，浏览器访问到的还是旧进程。
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 必须尽早执行：某些 Windows 机器上 socket.getfqdn() 会卡 10 秒以上，
# 而 http.server 在绑定端口时会调用它，导致"服务启动了但网页很久打不开"。
from core.netutil import patch_fast_getfqdn  # noqa: E402

patch_fast_getfqdn()


# --------------------------------------------------------------------------- #
# Flask 子进程
# --------------------------------------------------------------------------- #
def run_web(port: int) -> None:
    from app import create_app
    from core import service_control
    from core.config import DATA_DIR, HOST, load_config
    from core.log import get_logger, setup_logging
    from core.netutil import describe_port_owner, is_port_free

    cfg = load_config()
    setup_logging(DATA_DIR / "logs", cfg["log"]["level"], cfg["log"]["keep_days"])
    logger = get_logger("web")
    # 关键：Windows 上 SO_REUSEADDR 会让"端口已占用"也绑定成功，
    # 于是 Flask 打印了启动日志但请求被别的进程接走 → 网页打不开。
    # 这里先独占探测，占用就明确报错退出，而不是假装启动成功。
    if not is_port_free(port):
        if _health_ok(port):
            message = f"端口 {port} 上已经有一个 Flask 微内核实例在运行，无需重复启动。"
        else:
            message = f"{describe_port_owner(port)}，无法启动 Web 服务。"
        logger.error(message)
        print(f"[错误] {message}")
        print("[提示] 改 config.json 的 app.port，或用 python main.py 让内核自动改用备用端口。")
        raise SystemExit(3)

    app = create_app()
    # 实际监听端口以命令行参数为准（守护进程可能已按可用性换过端口）
    try:
        managed = os.environ.get("OA_UNDER_GUARDIAN") == "1"
        service_control.write_app_state(
            port=int(port), state="running",
            mode="guardian" if managed else "standalone",
        )
    except Exception:
        pass

    logger.info("Flask 启动：http://%s:%s/", HOST, port)
    app.run(host=HOST, port=int(port), threaded=True,
            use_reloader=False, debug=False)


def _health_ok(port: int, timeout: float = 2.0) -> bool:
    """探测端口上是不是已经有一个 Flask 微内核实例（用于识别"重复启动"）。"""
    import json as _json
    import urllib.request

    try:
        url = f"http://127.0.0.1:{port}/system/health"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = _json.loads(response.read().decode("utf-8", "replace"))
            return bool(data.get("ok"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 守护子进程
# --------------------------------------------------------------------------- #
def run_guardian(port: int) -> None:
    from core.config import DATA_DIR, load_config
    from core.guardian import Guardian
    from core.log import setup_logging

    cfg = load_config()
    setup_logging(DATA_DIR / "logs", cfg["log"]["level"], cfg["log"]["keep_days"])
    Guardian(int(port), base_dir=BASE_DIR).run()


# --------------------------------------------------------------------------- #
# UI 主进程
# --------------------------------------------------------------------------- #
def run_ui(port: int) -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("未安装 PySide6，无法启动桌面 UI。")
        print("请运行: pip install PySide6")
        wait_forever()
        return

    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow(port)
    win.show()
    sys.exit(app.exec())


def wait_forever() -> None:
    """没有桌面面板时，阻塞主进程让守护进程继续跑。"""
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("已停止")


# --------------------------------------------------------------------------- #
# 端口预检
# --------------------------------------------------------------------------- #
def resolve_runtime_port(logger) -> int:
    """按配置选端口；被占用时自动回退并写清楚原因。"""
    from core.config import load_config
    from core.netutil import describe_port_owner, resolve_port

    cfg = load_config()
    preferred = int((cfg.get("app") or {}).get("port") or 80)
    try:
        port, fell_back = resolve_port(preferred)
    except RuntimeError as exc:
        logger.error("%s", exc)
        print(f"[错误] {exc}")
        raise SystemExit(2)

    if fell_back:
        logger.warning(
            "配置端口 %s 不可用：%s；已自动改用 %s",
            preferred, describe_port_owner(preferred), port,
        )
        print(f"[提示] 端口 {preferred} 被占用，已自动改用 {port}")
        print(f"[提示] 想固定端口，请改 config.json 的 app.port 或系统设置页")
    else:
        logger.info("端口 %s 可用", port)
    return port


# --------------------------------------------------------------------------- #
# 单实例 / 残留进程处理
# --------------------------------------------------------------------------- #
def running_instance() -> int | None:
    """已经有健康的 Flask 微内核实例运行时，返回它的端口。"""
    from core import service_control
    from core.procutil import pid_alive

    runtime = service_control.read_runtime()
    guardian = runtime.get("guardian") or {}
    pid = guardian.get("pid")
    if not pid or not pid_alive(int(pid)):
        return None
    if not service_control.guardian_alive():
        return None
    # 守护进程记录的端口最权威（它负责实际拉起 app）
    port = guardian.get("port") or (runtime.get("app") or {}).get("port")
    app_pid = (runtime.get("app") or {}).get("pid")
    if not port or not app_pid or not pid_alive(int(app_pid)):
        return None
    if _health_ok(int(port)):
        return int(port)
    return None


def reap_stale_processes(logger) -> None:
    """清理上次被强制关闭后残留的守护进程 / app 进程。

    这是本机"网页打不开"最常见的根因：
    1. 直接关窗口时守护进程被 TerminateProcess 结束，finally 不执行；
    2. 这份 venv 的 python.exe 还会再拉起一个子进程，
       所以必须按进程**树**结束（taskkill /T），否则真正的 Flask 会变成孤儿；
    3. 孤儿继续占着端口，下次启动的实例绑不上，浏览器看到的还是旧进程。

    另外：如果配置端口被一个"能响应 /system/health 的旧实例"占着，
    说明那也是本项目的残留，一并清掉。
    """
    from core import service_control
    from core.config import load_config
    from core.netutil import find_pid_on_port
    from core.procutil import kill_pid, pid_alive

    runtime = service_control.read_runtime()
    guardian = runtime.get("guardian") or {}
    app_state = runtime.get("app") or {}

    targets: list[tuple[str, int]] = []
    if guardian.get("pid"):
        targets.append(("守护进程", int(guardian["pid"])))
    # 只有"由守护托管"或"状态未知"的 app 才当作残留；
    # 用户手动 --web 起的独立实例不去动它。
    app_mode = app_state.get("mode") or "unknown"
    if app_state.get("pid") and app_mode in ("guardian", "unknown"):
        targets.append(("应用进程", int(app_state["pid"])))

    killed = 0
    for label, pid in targets:
        if pid and pid_alive(pid) and pid != os.getpid():
            logger.warning("清理残留%s PID=%s", label, pid)
            kill_pid(pid, logger)  # taskkill /F /T → 连子进程一起结束
            killed += 1

    # 配置端口上如果还挂着一个"旧实例"，说明是更早的孤儿进程
    try:
        configured = int((load_config().get("app") or {}).get("port") or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured and _health_ok(configured):
        for pid in find_pid_on_port(configured):
            if pid != os.getpid() and pid_alive(pid):
                logger.warning("端口 %s 被旧实例占用（PID=%s），清理它", configured, pid)
                kill_pid(pid, logger)
                killed += 1

    if killed:
        time.sleep(0.8)
        service_control.mark_guardian_offline()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def parse_args():
    parser = argparse.ArgumentParser(description="Flask微内核（Flask 微内核）")
    parser.add_argument("--web", type=int, metavar="PORT", help="以 Web 模式运行")
    parser.add_argument("--guardian", type=int, metavar="PORT", help="以守护模式运行")
    parser.add_argument("--ui", type=int, metavar="PORT", help="以 UI 模式运行")
    parser.add_argument("--force", action="store_true",
                        help="已有实例在运行时强制接管（结束旧实例后重新启动）")
    return parser.parse_args()


def main() -> None:
    multiprocessing.freeze_support()
    args = parse_args()

    if args.web is not None:
        run_web(args.web)
        return

    if args.guardian is not None:
        run_guardian(args.guardian)
        return

    from core.config import DATA_DIR, load_config
    from core.log import setup_logging

    cfg = load_config()
    setup_logging(DATA_DIR / "logs", cfg["log"]["level"], cfg["log"]["keep_days"])
    from core.log import get_logger

    logger = get_logger("main")

    if args.ui is not None:
        run_ui(args.ui)
        return

    existing = running_instance()
    if existing and not args.force:
        message = (f"Flask微内核已经在运行（端口 {existing}），不需要重复启动。"
                   f" 想强制重启请加 --force")
        logger.info(message)
        print(f"[提示] {message}")
        _open_browser(existing)
        return

    if existing and args.force:
        logger.warning("--force：结束运行中的实例（端口 %s）", existing)
        reap_stale_processes(logger)

    reap_stale_processes(logger)
    port = resolve_runtime_port(logger)

    # 先起守护进程（它负责拉起 Flask 子进程）
    guardian = multiprocessing.Process(
        target=run_guardian, args=(port,), daemon=True, name="oa-guardian",
    )
    guardian.start()
    logger.info("守护进程已启动 PID=%s，Web 监听端口 %s", guardian.pid, port)
    time.sleep(1.2)

    try:
        run_ui(port)
    finally:
        shutdown(port, guardian, logger)


def _open_browser(port: int) -> None:
    import webbrowser

    try:
        from core.config import load_config

        home = (load_config().get("kernel") or {}).get("home_url") or "/"
    except Exception:
        home = "/"
    try:
        webbrowser.open(f"http://127.0.0.1:{port}{home}")
    except Exception:
        pass


def shutdown(port: int, guardian, logger) -> None:
    """退出顺序：让守护自己停服务 → 结束守护进程树 → 兜底清理端口。"""
    from core import service_control
    from core.procutil import kill_by_port, kill_pid

    if guardian is not None and guardian.is_alive():
        try:
            service_control.request_control("stop", requested_by="main-exit")
            logger.info("已请求守护进程停止服务")
        except Exception:
            logger.exception("写停止指令失败")

        deadline = time.time() + 6
        while time.time() < deadline and guardian.is_alive():
            if service_control.read_runtime().get("state") == "stopped":
                break
            time.sleep(0.2)

        if guardian.is_alive():
            logger.info("结束守护进程树 PID=%s", guardian.pid)
            # 注意：不能用 guardian.terminate()。这份 venv 的 python.exe 会再起
            # 一个子进程，TerminateProcess 只杀外层壳，真正的守护和 Flask 会变孤儿，
            # 继续占着端口 → 下次启动"网页打不开"。必须按进程树结束。
            kill_pid(guardian.pid, logger)
            guardian.join(timeout=8)

    # 兜底：端口上还有残留进程就清掉
    kill_by_port(port, logger)
    logger.info("已退出")


if __name__ == "__main__":
    main()
