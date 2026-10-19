/* ==========================================================================
   pages/settings.js —— 系统设置：密钥显隐、JSON 校验、保存提示
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App;

  // 密钥显示 / 隐藏
  App.$$('[data-reveal]').forEach(function (button) {
    button.addEventListener('click', function () {
      const input = button.parentElement.querySelector('input');
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.textContent = show ? '隐藏' : '显示';
    });
  });

  // 开关文字跟随
  App.$$('.setting-row input[type="checkbox"]').forEach(function (box) {
    box.addEventListener('change', function () {
      const label = box.parentElement.querySelector('.switch-label');
      if (label) label.textContent = box.checked ? '已开启' : '已关闭';
    });
  });

  // 主表单：提交前检查
  const form = App.$('#settingsForm');
  if (form) {
    form.addEventListener('submit', function (event) {
      const auto = form.querySelector('[name="auto_restart"]');
      const submit = App.$('#saveSettings');
      if (submit) submit.disabled = true;
      if (auto && auto.checked) {
        App.toast('正在保存配置并重启服务…', 'info', 8000);
      }
      // 不阻止提交：服务端会渲染"重启中"页面
      void event;
    });
  }

  // 高级 JSON：提交前校验格式
  const advanced = App.$('#advancedForm');
  if (advanced) {
    advanced.addEventListener('submit', function (event) {
      const textarea = App.$('#advancedJson');
      try {
        const parsed = JSON.parse(textarea.value);
        if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
          throw new Error('顶层必须是对象');
        }
      } catch (err) {
        event.preventDefault();
        App.toast('JSON 格式错误：' + err.message, 'error', 8000);
      }
    });
  }
})();
