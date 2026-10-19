/* ==========================================================================
   pages/users.js —— 用户管理：新建 / 编辑 / 改密 / 删除
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App;
  const base = location.pathname.replace(/\/+$/, '').replace(/\/users$/, '') || '/admin';
  const usersUrl = base + '/users';

  // ---------------------------------------------------------------- 工具
  function scope(prefix) {
    return App.$('[data-perm-scope="' + prefix + '"]');
  }

  function checkedPerms(root) {
    return App.$$('input[name="permissions"]:checked', root).map(i => i.value);
  }

  function setPerms(root, list) {
    const set = new Set(list || []);
    App.$$('input[name="permissions"]', root).forEach(function (input) {
      input.checked = set.has(input.value);
    });
  }

  // 权限组全选（只在所属 modal 内生效）
  App.$$('[data-perm-toggle-group]').forEach(function (button) {
    button.addEventListener('click', function () {
      const group = button.closest('.perm-group');
      const boxes = App.$$('input[name="permissions"]', group);
      const allChecked = boxes.every(b => b.checked);
      boxes.forEach(b => { b.checked = !allChecked; });
    });
  });

  // ---------------------------------------------------------------- 打开 / 关闭
  // 关闭按钮、点遮罩、Esc 都由 App.modal.init() 统一处理（app.js），这里不再重复绑定

  // ---------------------------------------------------------------- 新建
  const newModal = App.$('#newUserModal');
  const btnNew = App.$('#btnNewUser');
  if (btnNew) {
    btnNew.addEventListener('click', function () {
      const form = App.$('#newUserForm');
      if (form) form.reset();
      const root = scope('new');
      if (root) setPerms(root, []);
      App.modal.open('newUserModal');
    });
  }

  const newForm = App.$('#newUserForm');
  if (newForm) {
    newForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      const button = App.$('#newUserSubmit');
      const restore = App.busy(button, '创建中…');
      const root = scope('new');
      const payload = {
        username: newForm.querySelector('[name="username"]').value,
        password: newForm.querySelector('[name="password"]').value,
        active: newForm.querySelector('[name="active"]').checked,
        is_superadmin: newForm.querySelector('[name="is_superadmin"]')
          ? newForm.querySelector('[name="is_superadmin"]').checked : false,
        permissions: checkedPerms(root || newForm)
      };

      try {
        const result = await App.api(usersUrl + '/create', { method: 'POST', body: payload });
        App.toast(result.message || '用户已创建', 'success');
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        App.toast(err.message, 'error');
        restore();
      }
    });
  }

  // ---------------------------------------------------------------- 编辑
  App.$$('[data-edit]').forEach(function (button) {
    button.addEventListener('click', function () {
      const row = button.closest('tr');
      let perms = [];
      try { perms = JSON.parse(row.getAttribute('data-perms') || '[]'); } catch (e) { perms = []; }

      App.$('#editUserId').value = row.getAttribute('data-id');
      App.$('#editUsername').value = row.getAttribute('data-username');
      App.$('#editActive').checked = row.getAttribute('data-active') === '1';
      const superBox = App.$('#editSuperadmin');
      if (superBox) superBox.checked = row.getAttribute('data-superadmin') === '1';
      App.$('#editUserLabel').textContent = row.getAttribute('data-username');
      setPerms(scope('edit'), perms);

      App.modal.open('editUserModal');
    });
  });

  const editForm = App.$('#editUserForm');
  if (editForm) {
    editForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      const id = App.$('#editUserId').value;
      const button = App.$('#editUserSubmit');
      const restore = App.busy(button, '保存中…');
      const superBox = App.$('#editSuperadmin');
      const payload = {
        username: App.$('#editUsername').value,
        active: App.$('#editActive').checked,
        permissions: checkedPerms(scope('edit') || editForm)
      };
      if (superBox) payload.is_superadmin = superBox.checked;

      try {
        const result = await App.api(usersUrl + '/' + id + '/update',
                                    { method: 'POST', body: payload });
        App.toast(result.message || '已保存', 'success');
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        App.toast(err.message, 'error');
        restore();
      }
    });
  }

  // ---------------------------------------------------------------- 重置密码
  App.$$('[data-reset]').forEach(function (button) {
    button.addEventListener('click', function () {
      const row = button.closest('tr');
      App.$('#resetUserId').value = row.getAttribute('data-id');
      App.$('#resetUserName').textContent = row.getAttribute('data-username');
      App.$('#resetPassword').value = '';
      App.modal.open('resetModal');
    });
  });

  const resetForm = App.$('#resetForm');
  if (resetForm) {
    resetForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      const id = App.$('#resetUserId').value;
      const button = App.$('#resetSubmit');
      const restore = App.busy(button, '重置中…');
      try {
        const result = await App.api(usersUrl + '/' + id + '/reset-password', {
          method: 'POST',
          body: { password: App.$('#resetPassword').value }
        });
        App.toast(result.message || '密码已重置', 'success');
        App.modal.close('resetModal');
      } catch (err) {
        App.toast(err.message, 'error');
      } finally {
        restore();
      }
    });
  }

  // ---------------------------------------------------------------- 删除
  App.$$('[data-delete]').forEach(function (button) {
    button.addEventListener('click', async function () {
      const row = button.closest('tr');
      const name = row.getAttribute('data-username');
      const ok = await App.confirm({
        title: '删除用户',
        message: '确定删除用户「' + name + '」吗？该操作不可恢复。',
        confirmText: '删除',
        danger: true
      });
      if (!ok) return;

      const restore = App.busy(button, '');
      try {
        const result = await App.api(usersUrl + '/' + row.getAttribute('data-id') + '/delete',
                                     { method: 'POST' });
        App.toast(result.message || '已删除', 'success');
        row.remove();
        setTimeout(() => location.reload(), 700);
      } catch (err) {
        App.toast(err.message, 'error');
        restore();
      }
    });
  });

  // ---------------------------------------------------------------- 搜索
  const search = App.$('#userSearch');
  if (search) {
    search.addEventListener('input', App.debounce(function () {
      const keyword = search.value.trim().toLowerCase();
      let visible = 0;
      App.$$('#userTable tbody tr').forEach(function (row) {
        const name = (row.getAttribute('data-username') || '').toLowerCase();
        const show = !keyword || name.indexOf(keyword) >= 0;
        row.classList.toggle('hidden', !show);
        if (show) visible += 1;
      });
      const empty = App.$('#userEmpty');
      if (empty) {
        empty.classList.toggle('hidden', visible !== 0);
        if (!visible) {
          empty.innerHTML = '<div class="empty"><div class="empty-ico">' +
            '<svg class="i i-lg"><use href="#i-search"></use></svg></div>' +
            '<h3>没有匹配的用户</h3><p>换个关键字试试</p></div>';
        }
      }
    }, 160));
  }
})();
