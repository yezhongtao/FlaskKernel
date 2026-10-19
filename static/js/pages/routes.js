/* ==========================================================================
   pages/routes.js —— URL 映射页：行内修改路径 + 冷启动重启
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App;
  const basePath = location.pathname.replace(/\/+$/, '');

  App.$$('form[data-remap]').forEach(function (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      const routeId = form.getAttribute('data-route-id');
      const input = form.querySelector('input[name="path"]');
      const button = form.querySelector('button[type="submit"]');
      const newPath = (input.value || '').trim();

      if (!newPath.startsWith('/')) {
        App.toast('路径必须以 / 开头', 'warning');
        return;
      }

      const restore = App.busy(button, '保存中');
      try {
        const result = await App.api(basePath + '/' + routeId + '/remap', {
          method: 'POST',
          body: { path: newPath }
        });
        App.toast(result.message || '已保存，等待重启…', 'success');
        if (result.mode === 'guardian' && result.task_id) {
          await App.waitForRestart(result.task_id);
        }
        location.reload();
      } catch (err) {
        App.toast(err.message, 'error');
        restore();
      }
    });
  });

  const refresh = App.$('#refreshRoutes');
  if (refresh) refresh.addEventListener('click', () => location.reload());
})();
