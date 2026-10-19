/* ==========================================================================
   pages/logs.js —— 日志查看：应用 / 访问 / 控制台
   ========================================================================== */
(function () {
  'use strict';

  const App = window.App;
  const base = location.pathname.replace(/\/+$/, '');
  const dataUrl = base + '/data.json';

  let kind = 'app';
  let timer = null;

  const textBox = App.$('#logText');
  const tableWrap = App.$('#logTableWrap');
  const tableBody = App.$('#logTableBody');
  const fileSelect = App.$('#logFile');
  const daySelect = App.$('#logDay');
  const typeSelect = App.$('#logType');
  const linesSelect = App.$('#logLines');
  const searchWrap = App.$('#logSearchWrap');
  const queryInput = App.$('#logQuery');
  const autoBox = App.$('#logAuto');

  function esc(text) {
    return App.escape(text == null ? '' : text);
  }

  function setKind(next) {
    kind = next;
    App.$$('.tab[data-log-kind]').forEach(function (tab) {
      tab.classList.toggle('active', tab.getAttribute('data-log-kind') === next);
    });
    App.$$('[data-for]').forEach(function (el) {
      const groups = (el.getAttribute('data-for') || '').split(',');
      el.style.display = groups.indexOf(next) >= 0 ? '' : 'none';
    });
    if (searchWrap) searchWrap.style.display = next === 'access' ? '' : 'none';
    load();
  }

  function params() {
    const search = new URLSearchParams();
    search.set('lines', linesSelect ? linesSelect.value : '300');
    if (kind === 'access') {
      search.set('kind', 'access');
      if (daySelect && daySelect.value) search.set('day', daySelect.value);
      if (typeSelect && typeSelect.value) search.set('type', typeSelect.value);
      if (queryInput && queryInput.value) search.set('q', queryInput.value.trim());
    } else if (kind === 'console') {
      search.set('kind', 'app');
      search.set('file', 'console.log');
    } else {
      search.set('kind', 'app');
      let file = 'app.log';
      if (fileSelect) {
        const option = fileSelect.options[fileSelect.selectedIndex];
        if (option && !option.getAttribute('data-console')) file = option.value;
      }
      search.set('file', file);
    }
    return search.toString();
  }

  async function load() {
    if (!textBox || !tableWrap) return;
    try {
      const data = await App.api(dataUrl + '?' + params());
      render(data);
    } catch (err) {
      if (textBox) textBox.textContent = '读取日志失败：' + err.message;
    }
  }

  function render(data) {
    if (kind === 'access') {
      textBox.classList.add('hidden');
      tableWrap.classList.remove('hidden');
      renderAccess(data.rows || []);
      return;
    }
    tableWrap.classList.add('hidden');
    textBox.classList.remove('hidden');

    if (!data.ok) {
      textBox.textContent = data.error || '没有日志';
      return;
    }
    const lines = data.lines || [];
    if (!lines.length) {
      textBox.textContent = '（日志为空）';
      return;
    }
    textBox.innerHTML = lines.map(function (line) {
      let cls = '';
      if (line.indexOf('ERROR') >= 0) cls = 'lv-ERROR';
      else if (line.indexOf('WARNING') >= 0) cls = 'lv-WARNING';
      else if (line.indexOf('INFO') >= 0) cls = 'lv-INFO';
      return '<span class="log-line ' + cls + '">' + esc(line) + '</span>';
    }).join('\n');
    textBox.scrollTop = textBox.scrollHeight;
  }

  function renderAccess(rows) {
    if (!tableBody) return;
    if (!rows.length) {
      tableBody.innerHTML = '<tr><td colspan="6" class="muted center">没有匹配的访问记录</td></tr>';
      return;
    }
    tableBody.innerHTML = rows.map(function (row) {
      const isHttp = row.type === 'http';
      const source = isHttp
        ? esc(row.ip) + '<br><span class="muted tiny">' + esc(row.user) + '</span>'
        : esc(row.caller) + '<br><span class="muted tiny">→ ' + esc(row.provider) + '</span>';
      const target = isHttp
        ? '<span class="mono">' + esc(row.method) + '</span> ' + esc(row.path)
        : '<span class="mono">' + esc(row.service) + '.' + esc(row.method) + '</span>';
      const status = isHttp
        ? '<span class="badge badge-' + (row.status && row.status < 400 ? 'success' : 'danger') + '">' +
          esc(row.status) + '</span>'
        : '<span class="badge badge-' + (row.ok ? 'success' : 'danger') + '">' +
          (row.ok ? 'OK' : 'ERR') + '</span>';
      return '<tr>' +
        '<td class="mono tiny">' + esc((row.time || '').replace('T', ' ')) + '</td>' +
        '<td>' + (isHttp ? 'HTTP' : '插件调用') + '</td>' +
        '<td class="tiny">' + source + '</td>' +
        '<td class="small break">' + target + '</td>' +
        '<td>' + status + '</td>' +
        '<td class="mono tiny">' + esc(row.duration_ms || 0) + ' ms</td>' +
        '</tr>';
    }).join('');
  }

  function startAuto() {
    stopAuto();
    timer = setInterval(load, 3000);
  }
  function stopAuto() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  App.$$('.tab[data-log-kind]').forEach(function (tab) {
    tab.addEventListener('click', function () {
      setKind(tab.getAttribute('data-log-kind'));
    });
  });

  [fileSelect, daySelect, typeSelect, linesSelect].forEach(function (el) {
    if (el) el.addEventListener('change', load);
  });

  if (queryInput) {
    queryInput.addEventListener('input', App.debounce(load, 320));
  }

  const reload = App.$('#reloadLogs');
  if (reload) reload.addEventListener('click', load);

  if (autoBox) {
    autoBox.addEventListener('change', function () {
      if (autoBox.checked) startAuto(); else stopAuto();
    });
  }

  setKind('app');
})();
