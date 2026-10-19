/* 权限总览：点「眼睛」看权限全码、说明、持有人 */
(function () {
  'use strict';

  function readJson(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent || '') || fallback;
    } catch (e) {
      return fallback;
    }
  }

  const DETAIL = readJson('permDetailData', {});
  const SUPERS = readJson('permSuperData', []);

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function fill(detail) {
    setText('permDetailLabel', detail.label || detail.code || '—');
    setText('permDetailCode', detail.code || '—');
    setText('permDetailGroup', (detail.kind === 'plugin' ? '插件 · ' : '内核 · ') +
      (detail.group || '—'));

    const holders = Array.isArray(detail.holders) ? detail.holders : [];
    setText('permDetailCount', holders.length
      ? holders.length + ' 个账号显式拥有'
      : '没有账号显式拥有（仅超管可用）');

    const box = document.getElementById('permDetailHolders');
    if (box) {
      box.innerHTML = '';
      if (holders.length) {
        holders.forEach(function (name) {
          const chip = document.createElement('span');
          chip.className = 'badge badge-brand';
          chip.textContent = name;
          box.appendChild(chip);
        });
      } else {
        const tip = document.createElement('span');
        tip.className = 'tiny muted';
        tip.textContent = '还没有给任何账号勾选这项权限。';
        box.appendChild(tip);
      }
    }

    const note = document.getElementById('permSuperNote');
    if (note) {
      const supers = SUPERS.filter(function (name) { return name; });
      note.style.display = supers.length ? '' : 'none';
      setText('permSuperNames', supers.join('、'));
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    // 关闭按钮 / 点遮罩 / Esc 由 App.modal.init() 统一处理；这里再兜一层，
    // 保证这个弹窗在任何加载顺序下都能关掉。
    if (window.App && App.modal && App.modal.init) App.modal.init();
    document.querySelectorAll('#permDetailModal [data-close-modal]').forEach(function (button) {
      button.addEventListener('click', function () {
        if (window.App && App.modal) App.modal.close('permDetailModal');
      });
    });
    const backdrop = document.getElementById('permDetailModal');
    if (backdrop) {
      backdrop.addEventListener('click', function (event) {
        if (event.target === backdrop) {
          backdrop.classList.remove('show');
          document.body.style.overflow = '';
        }
      });
    }
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      const el = document.getElementById('permDetailModal');
      if (el && el.classList.contains('show')) {
        el.classList.remove('show');
        document.body.style.overflow = '';
      }
    });

    document.querySelectorAll('[data-perm-detail]').forEach(function (button) {
      button.addEventListener('click', function () {
        const code = button.getAttribute('data-perm-detail') || '';
        const detail = DETAIL[code] || { code: code, label: code, holders: [] };
        fill(detail);
        if (window.App && App.modal) App.modal.open('permDetailModal');
      });
    });

    const copy = document.getElementById('permCopyCode');
    if (copy) {
      const original = copy.innerHTML;
      copy.addEventListener('click', function () {
        const code = (document.getElementById('permDetailCode') || {}).textContent || '';
        navigator.clipboard.writeText(code).then(function () {
          copy.textContent = '已复制';
          setTimeout(function () { copy.innerHTML = original; }, 1500);
        });
      });
    }
  });
})();
