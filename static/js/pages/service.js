/* ==========================================================================
   pages/service.js —— 服务控制台
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App;
  const base = location.pathname.replace(/\/+$/, '');
  const statusUrl = base + '/status.json';
  const actionUrl = base + '/action';
  const safeModeUrl = base + '/safe-mode';

  let timer = null;
  let busy = false;

  // ---------------------------------------------------------------- 渲染
  const ring = App.$('#stateRing');
  const stateText = App.$('#stateText');
  const stateBadge = App.$('#stateBadge');
  const stateDesc = App.$('#stateDesc');
  const metaPort = App.$('#metaPort');
  const metaPid = App.$('#metaPid');
  const metaUptime = App.$('#metaUptime');

  const TEXT = {
    running: '服务运行中',
    stopped: '服务已停止',
    pending: '服务启动中',
    starting: '服务启动中',
    unknown: '状态未知'
  };

  function paint(status) {
    if (!status) return;
    const state = status.state || 'unknown';
    if (ring) {
      ring.classList.remove('up', 'down', 'pending');
      ring.classList.add(state === 'running' ? 'up' : (state === 'stopped' ? 'down' : 'pending'));
    }
    if (stateText) stateText.textContent = TEXT[state] || ('服务状态：' + state);

    if (stateBadge) {
      const map = {
        running: ['success', 'running'],
        stopped: ['danger', 'stopped'],
        pending: ['warning', 'pending']
      };
      const item = map[state] || ['outline', state];
      stateBadge.innerHTML = '<span class="badge badge-' + item[0] + '">' + item[1] + '</span>';
    }

    if (metaPort) metaPort.textContent = status.port || '-';
    if (metaPid) metaPid.textContent = status.app_pid || '-';
    if (metaUptime) metaUptime.textContent = formatUptime(status.uptime_seconds);

    if (stateDesc) {
      if (status.mode === 'guardian') {
        stateDesc.textContent = 'PID ' + (status.app_pid || '-') + ' · 端口 ' +
          (status.port || '-') + ' · 已运行 ' + formatUptime(status.uptime_seconds) +
          ' · 保活' + (status.keepalive ? '开启' : '关闭');
      } else {
        stateDesc.textContent = '当前没有守护进程（独立运行），只能停止服务；启动/重启请用桌面面板或 restart.bat。';
      }
    }

    const keep = App.$('#keepaliveSwitch');
    if (keep) {
      keep.checked = !!status.keepalive;
      const label = App.$('#keepaliveLabel');
      if (label) label.textContent = status.keepalive ? '已开启' : '已关闭';
    }
    const safe = App.$('#safeModeSwitch');
    if (safe) {
      safe.checked = !!status.safe_mode;
      const label = App.$('#safeModeLabel');
      if (label) label.textContent = status.safe_mode ? '已开启' : '已关闭';
    }
  }

  function formatUptime(seconds) {
    seconds = parseInt(seconds || 0, 10);
    if (!seconds) return '-';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d) return d + ' 天 ' + h + ' 小时';
    if (h) return h + ' 小时 ' + m + ' 分';
    if (m) return m + ' 分';
    return seconds + ' 秒';
  }

  async function refresh() {
    try {
      const data = await App.api(statusUrl);
      paint(data.status);
      return data.status;
    } catch (err) {
      if (stateDesc) {
        stateDesc.textContent = '无法连接服务：' + err.message +
          (App.$('#stateRing') ? '（服务可能已停止，等待守护进程接管）' : '');
      }
      return null;
    }
  }

  // ---------------------------------------------------------------- 轮询
  function startTimer() {
    stopTimer();
    timer = setInterval(refresh, 3000);
  }
  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  const auto = App.$('#autoRefresh');
  if (auto) {
    auto.addEventListener('change', function () {
      if (auto.checked) startTimer(); else stopTimer();
    });
  }

  // ---------------------------------------------------------------- 动作
  async function doAction(action, button) {
    if (busy) return;
    if (action === 'stop') {
      const ok = await App.confirm({
        title: '停止服务',
        message: '停止后当前页面会断开连接。若开启停机引导页，守护进程会在同一端口显示启动按钮。',
        confirmText: '停止服务',
        danger: true
      });
      if (!ok) return;
    }

    busy = true;
    const restore = App.busy(button, '下发中…');
    try {
      const result = await App.api(actionUrl, { method: 'POST', body: { action: action } });
      App.toast(result.message || '指令已下发', 'success', 6000);

      if (action === 'restart' && result.guardian && result.task_id) {
        restore();
        App.toast('等待服务重启完成…', 'info', 30000);
        await App.waitForRestart(result.task_id, { timeout: 90000 });
        location.reload();
        return;
      }

      if (action === 'stop') {
        restore();
        await waitStop();
        return;
      }

      if (action === 'start') {
        restore();
        await waitRunning();
        return;
      }
      restore();
      setTimeout(refresh, 1200);
    } catch (err) {
      App.toast(err.message, 'error', 8000);
      restore();
    } finally {
      busy = false;
    }
  }

  async function waitStop() {
    for (let i = 0; i < 30; i += 1) {
      await new Promise(r => setTimeout(r, 800));
      try {
        const data = await App.api(statusUrl);
        if (data.status && data.status.state === 'stopped') {
          paint(data.status);
          App.toast('服务已停止。刷新页面可看到停机引导页（若已开启）。', 'warning', 9000);
          return;
        }
      } catch (err) {
        App.toast('服务已停止，当前页面已断开连接。若开启了引导页，刷新即可看到启动按钮。',
                  'warning', 9000);
        if (ring) { ring.classList.remove('up'); ring.classList.add('down'); }
        if (stateText) stateText.textContent = '服务已停止';
        return;
      }
    }
  }

  async function waitRunning() {
    for (let i = 0; i < 40; i += 1) {
      await new Promise(r => setTimeout(r, 900));
      try {
        const data = await App.api(statusUrl);
        if (data.status && data.status.state === 'running') {
          paint(data.status);
          App.toast('服务已启动', 'success');
          return;
        }
      } catch (err) { /* 继续等 */ }
    }
    App.toast('启动超时，请查看日志或稍后刷新', 'warning', 8000);
  }

  App.$$('[data-service-action]').forEach(function (button) {
    button.addEventListener('click', function () {
      doAction(button.getAttribute('data-service-action'), button);
    });
  });

  // ---------------------------------------------------------------- 保活 / 安全模式
  const keepSwitch = App.$('#keepaliveSwitch');
  if (keepSwitch) {
    keepSwitch.addEventListener('change', async function () {
      const action = keepSwitch.checked ? 'keepalive_on' : 'keepalive_off';
      try {
        const result = await App.api(actionUrl, { method: 'POST', body: { action: action } });
        App.toast(result.message || '已更新', 'success');
      } catch (err) {
        App.toast(err.message, 'error');
        keepSwitch.checked = !keepSwitch.checked;
      }
      refresh();
    });
  }

  const safeSwitch = App.$('#safeModeSwitch');
  if (safeSwitch) {
    safeSwitch.addEventListener('change', async function () {
      try {
        const result = await App.api(safeModeUrl, {
          method: 'POST',
          body: { enabled: safeSwitch.checked }
        });
        App.toast(result.message || '已更新', 'warning', 7000);
      } catch (err) {
        App.toast(err.message, 'error');
        safeSwitch.checked = !safeSwitch.checked;
      }
      refresh();
    });
  }

  // ---------------------------------------------------------------- 初始化
  refresh();
  startTimer();
})();
