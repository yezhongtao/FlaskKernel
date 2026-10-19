/* ==========================================================================
   app.js —— 内核前端公共逻辑（无框架、无依赖）
   暴露全局对象 window.App，页面脚本只调用它。
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App = {};

  // ---------------------------------------------------------------- 选择器
  App.$ = (sel, root) => (root || document).querySelector(sel);
  App.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ---------------------------------------------------------------- 转义
  App.escape = function (text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

  // ---------------------------------------------------------------- Toast
  App.toast = function (message, type, timeout) {
    type = type || 'info';
    const stack = App.$('#toastStack') || (() => {
      const el = document.createElement('div');
      el.id = 'toastStack';
      el.className = 'toast-stack';
      document.body.appendChild(el);
      return el;
    })();

    const icons = { success: 'check', error: 'alert', warning: 'alert', info: 'info' };
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.innerHTML =
      '<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      iconPath(icons[type] || 'info') + '</svg>' +
      '<div class="grow">' + App.escape(message) + '</div>' +
      '<button class="toast-close" aria-label="关闭">×</button>';
    stack.appendChild(el);

    const close = () => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-6px)';
      el.style.transition = 'all .2s ease';
      setTimeout(() => el.remove(), 220);
    };
    el.querySelector('.toast-close').addEventListener('click', close);
    setTimeout(close, timeout || (type === 'error' ? 7000 : 4200));
    return close;
  };

  function iconPath(name) {
    const paths = {
      check: '<path d="M20 6L9 17l-5-5"/>',
      alert: '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.1l-8-14a2 2 0 0 0-3.4 0z"/>',
      info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
      x: '<path d="M18 6L6 18M6 6l12 12"/>'
    };
    return paths[name] || paths.info;
  }

  // ---------------------------------------------------------------- 请求
  App.api = async function (url, options) {
    options = options || {};
    const headers = Object.assign(
      { 'X-Requested-With': 'XMLHttpRequest' },
      options.headers || {}
    );
    let body = options.body;
    if (body && !(body instanceof FormData) && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(url, {
        method: options.method || 'GET',
        headers: headers,
        body: body,
        credentials: 'same-origin',
        cache: 'no-store'
      });
    } catch (err) {
      throw new Error('无法连接到服务（服务可能正在重启）：' + err.message);
    }

    let payload = null;
    const text = await response.text();
    if (text) {
      try { payload = JSON.parse(text); } catch (e) { payload = { error: text }; }
    }

    if (response.status === 401) {
      // 会话过期：直接回登录页，避免每个页面各自处理
      const next = encodeURIComponent(location.pathname + location.search);
      window.location.href = '/auth/login?next=' + next;
      throw new Error('登录已过期，正在跳转登录页…');
    }

    if (!response.ok || (payload && payload.ok === false)) {
      const message = (payload && (payload.error || payload.message)) ||
        ('请求失败（HTTP ' + response.status + '）');
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload || {};
  };

  // ---------------------------------------------------------------- 弹窗
  App.modal = {
    open: function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('show');
      document.body.style.overflow = 'hidden';
      const focusable = el.querySelector('input, select, textarea, button');
      if (focusable) setTimeout(() => focusable.focus(), 60);
    },
    close: function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.remove('show');
      if (!App.$('.modal-backdrop.show')) document.body.style.overflow = '';
    },
    /**
     * 全局弹窗行为（只绑定一次）：
     *   · 任何 [data-close-modal="id"] 按钮 → 关闭对应弹窗
     *   · 点遮罩空白处 → 关闭
     *   · Esc → 关闭最上层弹窗
     * 这样每个页面（含插件页）都不用各自重复写关闭逻辑。
     */
    init: function () {
      if (App.modal._bound) return;
      App.modal._bound = true;

      document.addEventListener('click', function (event) {
        const closer = event.target.closest && event.target.closest('[data-close-modal]');
        if (closer) {
          event.preventDefault();
          App.modal.close(closer.getAttribute('data-close-modal'));
          return;
        }
        if (event.target.classList && event.target.classList.contains('modal-backdrop')) {
          event.target.classList.remove('show');
          if (!App.$('.modal-backdrop.show')) document.body.style.overflow = '';
        }
      });

      document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        const opened = App.$$('.modal-backdrop.show');
        if (!opened.length) return;
        const top = opened[opened.length - 1];
        event.stopPropagation();
        if (top.id) {
          App.modal.close(top.id);
        } else {
          // App.confirm 之类的动态弹窗没有 id，直接关掉
          top.classList.remove('show');
          if (!App.$('.modal-backdrop.show')) document.body.style.overflow = '';
        }
      });
    }
  };

  App.confirm = function (options) {
    options = options || {};
    return new Promise(function (resolve) {
      const backdrop = document.createElement('div');
      backdrop.className = 'modal-backdrop show';
      backdrop.innerHTML =
        '<div class="modal" role="dialog" aria-modal="true">' +
        '  <div class="modal-head"><h3>' + App.escape(options.title || '请确认') + '</h3></div>' +
        '  <div class="modal-body"><p class="muted">' + App.escape(options.message || '') + '</p></div>' +
        '  <div class="modal-foot">' +
        '    <button class="btn" data-act="cancel">' + App.escape(options.cancelText || '取消') + '</button>' +
        '    <button class="btn ' + (options.danger ? 'btn-danger' : 'btn-primary') + '" data-act="ok">' +
             App.escape(options.confirmText || '确定') + '</button>' +
        '  </div></div>';
      document.body.appendChild(backdrop);
      document.body.style.overflow = 'hidden';

      function done(value) {
        backdrop.remove();
        document.body.style.overflow = '';
        resolve(value);
      }
      backdrop.addEventListener('click', function (event) {
        if (event.target === backdrop) done(false);
        const act = event.target.getAttribute && event.target.getAttribute('data-act');
        if (act === 'ok') done(true);
        if (act === 'cancel') done(false);
      });
      setTimeout(function () {
        const ok = backdrop.querySelector('[data-act="ok"]');
        if (ok) ok.focus();
      }, 40);
    });
  };

  // ---------------------------------------------------------------- 主题
  App.theme = {
    key: 'oa-theme',
    init: function () {
      let saved = null;
      try { saved = localStorage.getItem(App.theme.key); } catch (e) { /* 忽略 */ }
      const prefersDark = window.matchMedia &&
        window.matchMedia('(prefers-color-scheme: dark)').matches;
      App.theme.apply(saved || (prefersDark ? 'dark' : 'light'));
    },
    apply: function (name) {
      document.documentElement.setAttribute('data-theme', name);
      const btn = App.$('#themeToggle');
      if (btn) btn.title = name === 'dark' ? '切换到浅色' : '切换到深色';
      const icon = btn && btn.querySelector('use');
      if (icon) icon.setAttribute('href', name === 'dark' ? '#i-sun' : '#i-moon');
    },
    toggle: function () {
      const next = document.documentElement.getAttribute('data-theme') === 'dark'
        ? 'light' : 'dark';
      App.theme.apply(next);
      try { localStorage.setItem(App.theme.key, next); } catch (e) { /* 忽略 */ }
    }
  };

  // ---------------------------------------------------------------- 导航
  App.nav = {
    init: function () {
      const burger = App.$('#hamburger');
      const overlay = App.$('#sidebarOverlay');
      if (burger) {
        burger.addEventListener('click', function () {
          document.body.classList.toggle('nav-open');
          if (overlay) overlay.classList.toggle('show', document.body.classList.contains('nav-open'));
        });
      }
      if (overlay) {
        overlay.addEventListener('click', function () {
          document.body.classList.remove('nav-open');
          overlay.classList.remove('show');
        });
      }
      App.$$('.sidebar .nav-link').forEach(function (link) {
        link.addEventListener('click', function () {
          if (window.innerWidth <= 900) {
            document.body.classList.remove('nav-open');
            if (overlay) overlay.classList.remove('show');
          }
        });
      });

      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
          document.body.classList.remove('nav-open');
          if (overlay) overlay.classList.remove('show');
          App.$$('.dropdown.open').forEach(d => d.classList.remove('open'));
        }
      });
    }
  };

  // ---------------------------------------------------------------- 下拉菜单
  App.dropdown = {
    init: function () {
      App.$$('[data-dropdown-toggle]').forEach(function (toggle) {
        toggle.addEventListener('click', function (event) {
          event.stopPropagation();
          const wrap = toggle.closest('.dropdown');
          if (!wrap) return;
          const willOpen = !wrap.classList.contains('open');
          App.$$('.dropdown.open').forEach(d => d.classList.remove('open'));
          wrap.classList.toggle('open', willOpen);
        });
      });
      document.addEventListener('click', function () {
        App.$$('.dropdown.open').forEach(d => d.classList.remove('open'));
      });
    }
  };

  // ---------------------------------------------------------------- 重启轮询
  App.healthUrl = function () {
    return document.body.getAttribute('data-health-url') || '/system/health';
  };

  /**
   * 冷启动重启：先观察到服务不可用（seenDown），再观察到任务完成，才算成功。
   * 与方案 10.2 的 waitForRestart 一致，额外支持 onTick 回调与超时兜底。
   */
  App.waitForRestart = async function (taskId, options) {
    options = options || {};
    const timeout = options.timeout || 90000;
    const started = Date.now();
    let seenDown = false;

    while (Date.now() - started < timeout) {
      try {
        const response = await fetch(
          App.healthUrl() + '?task_id=' + encodeURIComponent(taskId),
          { cache: 'no-store' }
        );
        const data = await response.json();
        if (data && data.task && data.task.status === 'done' && seenDown) return data.task;
        if (data && data.ok && seenDown) return data.task || { status: 'done' };
        if (typeof options.onTick === 'function') options.onTick('up');
      } catch (err) {
        seenDown = true;
        if (typeof options.onTick === 'function') options.onTick('down');
      }
      await new Promise(resolve => setTimeout(resolve, 700));
    }
    throw new Error('等待服务重启超时，请刷新页面或查看日志');
  };

  // ---------------------------------------------------------------- 其它工具
  App.copy = async function (text) {
    try {
      await navigator.clipboard.writeText(text);
      App.toast('已复制到剪贴板', 'success', 2000);
    } catch (err) {
      App.toast('复制失败：' + err.message, 'error');
    }
  };

  App.timeAgo = function (iso) {
    if (!iso) return '-';
    const time = new Date(iso.replace(' ', 'T'));
    if (isNaN(time.getTime())) return iso;
    const diff = Math.floor((Date.now() - time.getTime()) / 1000);
    if (diff < 60) return diff + ' 秒前';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    return Math.floor(diff / 86400) + ' 天前';
  };

  App.debounce = function (fn, wait) {
    let timer = null;
    return function () {
      const args = arguments;
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(null, args), wait || 250);
    };
  };

  /** 按钮忙碌态：禁用 + 转圈，返回恢复函数 */
  App.busy = function (button, label) {
    if (!button) return function () {};
    const original = button.innerHTML;
    button.classList.add('is-loading');
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>' + (label || '处理中…');
    return function () {
      button.classList.remove('is-loading');
      button.disabled = false;
      button.innerHTML = original;
    };
  };

  // ---------------------------------------------------------------- 启动
  document.addEventListener('DOMContentLoaded', function () {
    App.theme.init();
    App.nav.init();
    App.dropdown.init();
    App.modal.init();

    const themeToggle = App.$('#themeToggle');
    if (themeToggle) themeToggle.addEventListener('click', App.theme.toggle);

    // 自动关闭 flash 提示
    App.$$('[data-auto-dismiss]').forEach(function (el) {
      setTimeout(function () {
        el.style.transition = 'opacity .3s ease';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 320);
      }, parseInt(el.getAttribute('data-auto-dismiss'), 10) || 5000);
    });

    // 返回按钮
    App.$$('[data-back]').forEach(function (el) {
      el.addEventListener('click', function () {
        if (window.history.length > 1) window.history.back();
        else window.location.href = '/';
      });
    });

    // 侧栏底部的服务状态小圆点
    const dot = App.$('[data-service-dot]');
    if (dot) {
      fetch(dot.getAttribute('data-status-url') || '/admin/service/status.json',
            { cache: 'no-store', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(r => r.ok ? r.json() : null)
        .then(function (data) {
          if (!data || !data.status) return;
          dot.classList.toggle('off', data.status.state !== 'running');
        })
        .catch(function () { dot.classList.add('off'); });
    }
  });
})();
