"""守护进程：监控 app、失败计数、安全模式、消费控制指令、停机引导页。

进程树（方案 2.1）：

    main.py（UI 主进程）
    ├── Guardian（本模块，守护子进程）
    │    └── app 子进程（Flask 服务，独立进程）
    └── PySide6 界面

守护进程职责（方案 2.3 + 服务控制扩展）：
1. 监控 app 进程存活，崩溃自动拉起；
2. 滑动窗口统计失败次数（默认 60 秒 / 5 次），超阈值进入安全模式；
3. 每次重启前延迟（默认 3 秒）；
4. 消费 data/control.json：start / stop / restart / keepalive_on / keepalive_off；
5. 消费 restart.flag：插件安装/启停/卸载后的冷启动重启；
6. app 被"停止服务"后，在同一个端口提供极简引导页，方便从手机重新点"启动"。

保活（keepalive）关闭后：app 崩溃不再自动拉起，但守护进程仍在，
所以仍然可以从 Web 控制台 / 桌面面板点"启动服务"。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import service_control
from core.alert import send_alert
from core.config import (BASE_DIR, LOGS_DIR, RESTART_FLAG, SAFE_MODE_FILE,
                         get_config, load_config, set_config_path)
from core.log import get_logger
from core.procutil import kill_by_port, kill_process_tree, pid_alive
from core.store import save_json

logger = get_logger("guardian")


# --------------------------------------------------------------------------- #
# 停机引导页（只在 app 停止且 service.status_page=true 时占用同一端口）
# --------------------------------------------------------------------------- #
_STATUS_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>服务已停止 - Flask微内核</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:radial-gradient(1200px 600px at 20% 0%,#1e293b,#0b1220 60%);
       font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#e2e8f0;padding:20px}
  .box{width:100%;max-width:420px;background:rgba(30,41,59,.92);border:1px solid #334155;
       border-radius:16px;padding:28px 24px;box-shadow:0 24px 60px rgba(0,0,0,.45)}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#ef4444;
       margin-right:8px;box-shadow:0 0 0 4px rgba(239,68,68,.18)}
  h1{font-size:19px;margin:0 0 8px}
  p{color:#94a3b8;font-size:13px;line-height:1.7;margin:0 0 18px}
  button{width:100%;padding:12px;margin-bottom:10px;border-radius:10px;border:1px solid transparent;
         font-size:14px;cursor:pointer;font-family:inherit}
  .primary{background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff}
  .ghost{background:transparent;border-color:#475569;color:#cbd5e1}
  button:active{transform:translateY(1px)}
  .msg{font-size:12px;color:#fca5a5;min-height:16px;text-align:center;margin-top:6px}
  code{background:#0f172a;padding:1px 6px;border-radius:5px;font-size:12px;color:#93c5fd}
</style></head>
<body><div class="box">
  <h1><span class="dot"></span>Flask微内核服务已停止</h1>
  <p>Flask 服务当前处于停止状态，守护进程仍在运行并保留了这个端口。
     你可以直接在这里重新启动，页面会自动跳转到登录页。</p>
  <button class="primary" onclick="act('start')">启动服务</button>
  <button class="ghost" onclick="act('restart')">重启服务</button>
  <div class="msg" id="msg"></div>
  <p style="margin-top:14px">端口 <code>__PORT__</code> · 守护进程 <code>__PID__</code></p>
  <script>
  async function act(kind){
    const msg=document.getElementById('msg');
    msg.textContent='正在执行…';
    try{
      const r=await fetch('/control/'+kind,{method:'POST'});
      const d=await r.json();
      msg.textContent = d.ok ? '指令已下发，等待服务启动…' : ('失败：'+(d.error||''));
      if(d.ok) setTimeout(()=>location.reload(),1600);
    }catch(e){ msg.textContent='失败：'+e.message; }
  }
  setTimeout(()=>location.reload(), 8000);
  </script>
</div></body></html>
"""


class _StatusHandler(BaseHTTPRequestHandler):
    server_version = "OAStatus/1.0"

    def log_message(self, fmt, *args):  # 静默，避免刷屏
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            html = (_STATUS_HTML
                    .replace("__PORT__", str(getattr(self.server, "oa_port", "-")))
                    .replace("__PID__", str(os.getpid())))
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/status.json":
            body = json.dumps({"state": "stopped"}, ensure_ascii=False)
            self._send(200, body.encode("utf-8"), "application/json")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?")[0]
        action = path.rsplit("/", 1)[-1]
        if action not in ("start", "restart"):
            self._send(404, b'{"ok":false}', "application/json")
            return
        try:
            service_control.request_control(action, requested_by="status-page")
            body = json.dumps({"ok": True, "action": action}, ensure_ascii=False)
            self._send(200, body.encode("utf-8"), "application/json")
        except Exception as exc:
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            self._send(500, body.encode("utf-8"), "application/json")


class Guardian:
    """守护进程主体。run() 会阻塞，直到收到停止信号。"""

    def __init__(self, port: int, base_dir: str | Path | None = None):
        self.port = int(port)
        self.base_dir = Path(base_dir or BASE_DIR)

        self.proc: subprocess.Popen | None = None
        self.failures: list[float] = []
        self.stop_requested = False
        self.keepalive = True
        self.next_spawn_at = 0.0
        self._expected_exit = False
        self._allow_spawn = True
        self._running = True
        self._last_config_at = 0.0
        self._last_state_at = 0.0
        self._console_fp = None

        self._status_server: ThreadingHTTPServer | None = None
        self._status_thread: threading.Thread | None = None

    # ===================== 生命周期 =====================
    def run(self) -> None:
        cfg = load_config()
        svc = cfg.get("service") or {}
        self.keepalive = bool(svc.get("keepalive", True))
        self._allow_spawn = bool(svc.get("auto_start", True))

        self._install_signal_handlers()

        # 单实例保护：已经有一个健康的守护进程在跑时直接退出，
        # 避免两个守护进程抢同一个端口、互相覆盖 runtime.json。
        existing = service_control.read_runtime().get("guardian") or {}
        existing_pid = existing.get("pid")
        if existing_pid and int(existing_pid) != os.getpid() \
                and service_control.guardian_alive() \
                and pid_alive(int(existing_pid)):
            logger.error(
                "已有守护进程在运行（PID=%s，端口 %s），本次启动退出",
                existing_pid, existing.get("port"),
            )
            self._running = False
            return

        logger.info(
            "守护进程启动：端口 %s，保活=%s，自动启动=%s",
            self.port, self.keepalive, self._allow_spawn,
        )
        if not self._port_free_for_app():
            logger.warning(
                "端口 %s 当前被占用（%s），app 可能无法启动",
                self.port, self._port_owner_hint(),
            )

        service_control.write_guardian_state(
            pid=os.getpid(), keepalive=self.keepalive,
            state="starting", port=self.port,
        )

        if self._allow_spawn:
            self._spawn_app()

        try:
            while self._running:
                self._tick()
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("守护进程收到 Ctrl+C")
        except Exception:
            logger.exception("守护进程异常退出")
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._running = False

    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info("守护进程收到信号 %s，准备退出", signum)
            self._running = False

        for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
            except (OSError, ValueError):
                pass

    def _shutdown(self) -> None:
        self._stop_status_page()
        if self.proc is not None:
            logger.info("守护进程退出，清理应用进程 PID=%s", self.proc.pid)
            kill_process_tree(self.proc, logger)
            self.proc = None
        self._close_console()
        service_control.clear_app_state("stopped")
        service_control.mark_guardian_offline()

    # ===================== 主循环 =====================
    def _tick(self) -> None:
        now = time.time()
        self._reload_config(now)
        self._consume_control()
        self._consume_restart_flag()
        self._watch_app(now)
        self._write_state(now)

    def _reload_config(self, now: float) -> None:
        if now - self._last_config_at < 2.0:
            return
        self._last_config_at = now
        try:
            cfg = load_config()
        except Exception:
            logger.exception("读取配置失败")
            return
        svc = cfg.get("service") or {}
        keepalive = bool(svc.get("keepalive", True))
        if keepalive != self.keepalive:
            logger.info("保活开关变更：%s -> %s", self.keepalive, keepalive)
            self.keepalive = keepalive

    # ---------- 控制指令 ----------
    def _consume_control(self) -> None:
        req = service_control.take_control()
        if not req:
            return
        action = req.get("action")
        logger.info("收到控制指令：%s（来自 %s）", action, req.get("requested_by"))
        try:
            self._apply_control(action, req)
        except Exception:
            logger.exception("执行控制指令失败：%s", action)

    def _apply_control(self, action: str, req: dict) -> None:
        if action == "keepalive_on":
            self.keepalive = True
            service_control.set_keepalive(True)
            logger.info("保活已开启")
        elif action == "keepalive_off":
            self.keepalive = False
            service_control.set_keepalive(False)
            logger.info("保活已关闭（app 崩溃后不再自动拉起，仍可手动启动）")
        elif action == "stop":
            self.stop_requested = True
            self._kill_app("stop")
            self._start_status_page()
            logger.info("服务已停止（守护进程保留端口 %s）", self.port)
        elif action == "start":
            self.stop_requested = False
            self._allow_spawn = True
            self.next_spawn_at = 0.0
            self._stop_status_page()
            self._spawn_app()
        elif action == "restart":
            self.stop_requested = False
            self._allow_spawn = True
            self.next_spawn_at = 0.0
            self._stop_status_page()
            self._kill_app("restart")
            if req.get("reload_port"):
                self._reload_port()
            self._spawn_app()

        # 记录任务完成，供前端 /system/health?task_id= 轮询
        task_id = req.get("task_id")
        if task_id:
            try:
                service_control.record_task(task_id, action)
            except Exception:
                logger.exception("记录控制任务失败")

    # ---------- restart.flag（冷启动重启） ----------
    def _consume_restart_flag(self) -> None:
        if not RESTART_FLAG.exists():
            return
        if self.proc is None or self.proc.poll() is not None:
            return
        logger.info("检测到 restart.flag，结束应用进程以完成冷启动")
        self.stop_requested = False
        self.next_spawn_at = 0.0
        self._stop_status_page()
        self._kill_app("restart.flag")

    # ---------- 监控 app ----------
    def _watch_app(self, now: float) -> None:
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self._close_console()
            expected = self._expected_exit
            self._expected_exit = False

            if expected:
                logger.info("应用进程已按指令退出（code=%s）", code)
            else:
                self.failures = [t for t in self.failures if now - t < self._window()]
                self.failures.append(now)
                logger.warning(
                    "应用进程异常退出（code=%s），%ss 内第 %d 次",
                    code, int(self._window()), len(self.failures),
                )
                if len(self.failures) >= self._threshold():
                    self._enter_safe_mode()
                    self.failures.clear()

            if self.stop_requested:
                self._start_status_page()
                return

            if self.keepalive and not self.stop_requested:
                self.next_spawn_at = now + self._restart_delay()
            else:
                logger.warning("保活已关闭，不再自动拉起应用进程")
                self.stop_requested = True
                self._start_status_page()

        if self.proc is None and self._allow_spawn and not self.stop_requested \
                and now >= self.next_spawn_at:
            self._spawn_app()

    def _reload_port(self) -> None:
        """配置里改了 app.port：重新解析端口并重启 app（Web 设置页会用到）。"""
        from core.netutil import describe_port_owner, resolve_port

        try:
            cfg = load_config()
            preferred = int((cfg.get("app") or {}).get("port") or self.port)
            port, fell_back = resolve_port(preferred)
        except Exception:
            logger.exception("重新解析端口失败，继续使用 %s", self.port)
            return

        if fell_back:
            logger.warning(
                "配置端口 %s 不可用（%s），改用 %s", preferred,
                describe_port_owner(preferred), port,
            )
        if port != self.port:
            logger.info("端口变更：%s -> %s（停掉停机引导页后重启）", self.port, port)
            self._stop_status_page()
            self.port = port
        service_control.write_guardian_state(
            pid=os.getpid(), keepalive=self.keepalive,
            state="running", port=self.port,
        )

    def _port_owner_hint(self) -> str:
        from core.netutil import describe_port_owner

        try:
            return describe_port_owner(self.port)
        except Exception:
            return "未知"

    # ---------- 启动 / 停止 app ----------
    def _spawn_app(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        cmd = self._build_cmd()
        self._stop_status_page()
        # 端口可能被上一条 app 进程的 TIME_WAIT 或残留进程占用
        if not self._port_free_for_app():
            logger.warning("启动前检测到端口 %s 不可用，尝试清理残留进程", self.port)
            kill_by_port(self.port, logger)
            time.sleep(0.4)

        self._expected_exit = False
        env = dict(os.environ)
        env["OA_UNDER_GUARDIAN"] = "1"   # app 用它标记"由守护进程托管"
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.base_dir), env=env,
                stdout=self._console_handle(), stderr=subprocess.STDOUT,
            )
            logger.info("启动应用进程 PID=%s: %s", self.proc.pid, " ".join(cmd))
            service_control.write_guardian_state(
                pid=os.getpid(), keepalive=self.keepalive,
                state="running", port=self.port, managed_pid=self.proc.pid,
            )
        except Exception:
            logger.exception("启动应用进程失败")
            self.proc = None
            self.next_spawn_at = time.time() + self._restart_delay()

    def _kill_app(self, reason: str) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            logger.info("结束应用进程 PID=%s（%s）", self.proc.pid, reason)
            self._expected_exit = True
            kill_process_tree(self.proc, logger)
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        self._close_console()

    def _build_cmd(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--web", str(self.port)]
        return [sys.executable, str(self.base_dir / "main.py"), "--web", str(self.port)]

    def _port_free_for_app(self) -> bool:
        from core.netutil import is_port_free

        try:
            return is_port_free(self.port)
        except Exception:
            return True

    def _console_handle(self):
        """app 的 stdout/stderr 追加到 data/logs/console.log，便于排查启动失败。"""
        if self._console_fp is None:
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                self._console_fp = open(LOGS_DIR / "console.log", "ab", buffering=0)
            except OSError:
                self._console_fp = subprocess.DEVNULL
        return self._console_fp

    def _close_console(self) -> None:
        fp = self._console_fp
        self._console_fp = None
        if fp is not None and fp is not subprocess.DEVNULL:
            try:
                fp.close()
            except OSError:
                pass

    # ---------- 配置读取 ----------
    @staticmethod
    def _service_cfg() -> dict:
        try:
            return (get_config() or {}).get("service") or {}
        except Exception:
            return {}

    def _window(self) -> float:
        return float(self._service_cfg().get("watch_window") or 60)

    def _threshold(self) -> int:
        return max(1, int(self._service_cfg().get("watch_threshold") or 5))

    def _restart_delay(self) -> float:
        return max(0.0, float(self._service_cfg().get("restart_delay") or 3))

    # ---------- 安全模式 ----------
    def _enter_safe_mode(self) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.error("连续失败超过阈值，进入安全模式（跳过所有插件加载）")
        try:
            save_json(SAFE_MODE_FILE, {"at": now, "port": self.port})
            set_config_path("plugin.safe_mode", True)
        except Exception:
            logger.exception("写入安全模式失败")
        send_alert(
            "OA 内核进入安全模式",
            f"{int(self._window())} 秒内应用进程失败超过 {self._threshold()} 次，"
            f"已自动跳过插件加载。请登录后在插件管理中停用问题插件。",
            level="error", port=self.port, time=now,
        )

    # ---------- 状态心跳 ----------
    def _write_state(self, now: float) -> None:
        if now - self._last_state_at < 2.0:
            return
        self._last_state_at = now
        running = self.proc is not None and self.proc.poll() is None
        state = "running" if running else ("stopped" if self.stop_requested else "pending")
        try:
            service_control.write_guardian_state(
                pid=os.getpid(), keepalive=self.keepalive, state=state,
                port=self.port,
                managed_pid=self.proc.pid if running else None,
            )
        except Exception:
            pass

    # ---------- 停机引导页 ----------
    def _start_status_page(self) -> None:
        if self._status_server is not None:
            return
        svc = self._service_cfg()
        if not svc.get("status_page", True):
            return
        # http.server 绑定端口时会调用 getfqdn（某些 Windows 机器卡 10 秒+）
        from core.netutil import patch_fast_getfqdn

        patch_fast_getfqdn()
        try:
            server = ThreadingHTTPServer(("0.0.0.0", self.port), _StatusHandler)
            server.oa_port = self.port  # type: ignore[attr-defined]
            server.daemon_threads = True
            thread = threading.Thread(
                target=server.serve_forever, name="oa-status-page", daemon=True,
            )
            thread.start()
            self._status_server = server
            self._status_thread = thread
            logger.info("已启动停机引导页：http://0.0.0.0:%s/", self.port)
        except OSError as exc:
            logger.warning("停机引导页绑定端口 %s 失败：%s", self.port, exc)
            self._status_server = None

    def _stop_status_page(self) -> None:
        server = self._status_server
        self._status_server = None
        self._status_thread = None
        if server is None:
            return
        try:
            server.shutdown()
            server.server_close()
            logger.info("已关闭停机引导页")
        except Exception:
            logger.exception("关闭停机引导页失败")
        time.sleep(0.15)  # 给 socket 一点释放时间，避免和 app 抢端口
