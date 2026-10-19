/* ==========================================================================
   pages/plugins.js —— 插件管理页
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App;

  const form = App.$('#uploadForm');
  const modal = App.$('#uploadModal');
  // 用当前页面地址作为基址，这样改过 admin_prefix 也不会失效
  const baseUrl = location.pathname.replace(/\/+$/, '') || '/admin/plugins';

  // ---------------------------------------------------------------- 安装
  function openUpload() {
    if (modal) modal.classList.add('show');
    document.body.style.overflow = 'hidden';
    const input = App.$('#pluginFile');
    if (input) setTimeout(() => input.focus(), 80);
  }
  function closeUpload() {
    if (modal) modal.classList.remove('show');
    document.body.style.overflow = '';
    if (form) form.reset();
  }

  App.$$('[data-open-upload]').forEach(b => b.addEventListener('click', openUpload));
  App.$$('[data-close-upload]').forEach(b => b.addEventListener('click', closeUpload));
  if (modal) {
    modal.addEventListener('click', function (event) {
      if (event.target === modal) closeUpload();
    });
  }

  if (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      const input = App.$('#pluginFile');
      if (!input.files.length) {
        App.toast('请选择 .zip 插件包', 'warning');
        return;
      }
      const button = App.$('#uploadBtn');
      const restore = App.busy(button, '上传中…');
      const data = new FormData();
      data.append('file', input.files[0]);

      try {
        const result = await App.api(baseUrl + '/upload', { method: 'POST', body: data });
        App.toast(result.message || '安装成功', 'success', 6000);
        closeUpload();
        if (result.mode === 'guardian' && result.task_id) {
          App.toast('等待冷启动完成…', 'info', 8000);
          await App.waitForRestart(result.task_id);
        }
        location.reload();
      } catch (err) {
        App.toast(err.message, 'error');
      } finally {
        restore();
      }
    });
  }

  // ---------------------------------------------------------------- 启停 / 卸载
  async function runAction(id, action, button) {
    const labels = { enable: '启用', disable: '停用', uninstall: '卸载' };
    const ok = await App.confirm({
      title: labels[action] + '插件',
      message: action === 'uninstall'
        ? '卸载会删除数据库记录并把插件目录移到 staging/。该操作不可撤销。'
        : '操作会触发冷启动重启，正在浏览的页面可能短暂不可用。',
      confirmText: labels[action],
      danger: action !== 'enable'
    });
    if (!ok) return;

    const restore = App.busy(button, '处理中…');
    try {
      const result = await App.api(baseUrl + '/' + encodeURIComponent(id) + '/' + action,
                                   { method: 'POST' });
      App.toast(result.message || (labels[action] + '指令已下发'), 'success');
      if (result.mode === 'guardian' && result.task_id) {
        await App.waitForRestart(result.task_id);
      }
      location.reload();
    } catch (err) {
      App.toast(err.message, 'error');
      restore();
    }
  }

  App.$$('[data-action]').forEach(function (button) {
    button.addEventListener('click', function () {
      runAction(button.getAttribute('data-id'), button.getAttribute('data-action'), button);
    });
  });

  // ---------------------------------------------------------------- 搜索 / 筛选
  const search = App.$('#pluginSearch');
  const statusFilter = App.$('#pluginStatus');
  const rows = App.$$('#pluginTable tbody tr');

  function applyFilter() {
    const keyword = (search && search.value || '').trim().toLowerCase();
    const status = statusFilter && statusFilter.value || '';
    let visible = 0;

    rows.forEach(function (row) {
      const matchText = !keyword || (row.getAttribute('data-search') || '').indexOf(keyword) >= 0;
      const matchStatus = !status || row.getAttribute('data-status') === status;
      const show = matchText && matchStatus;
      row.classList.toggle('hidden', !show);
      if (show) visible += 1;
    });

    const empty = App.$('#pluginEmpty');
    if (empty) empty.classList.toggle('hidden', visible !== 0);
    const table = App.$('#pluginTable');
    if (table) table.classList.toggle('hidden', visible === 0);
    const counter = App.$('#pluginCount');
    if (counter) counter.textContent = visible + ' 个插件';
  }

  if (search) search.addEventListener('input', App.debounce(applyFilter, 180));
  if (statusFilter) statusFilter.addEventListener('change', applyFilter);
})();
