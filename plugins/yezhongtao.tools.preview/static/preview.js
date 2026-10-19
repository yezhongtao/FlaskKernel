/* 文件预览插件的交互增强（页面主体是服务端渲染，这里只做锦上添花） */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    // 1) 当前目录过滤
    var filter = document.getElementById('pvFilter');
    var rows = Array.prototype.slice.call(document.querySelectorAll('#pvList .pv-row'));
    if (filter && rows.length) {
      filter.addEventListener('input', function () {
        var keyword = filter.value.trim().toLowerCase();
        rows.forEach(function (row) {
          var name = row.getAttribute('data-name') || '';
          row.style.display = !keyword || name.indexOf(keyword) !== -1 ? '' : 'none';
        });
      });
      // 按 "/" 快速定位到过滤框
      document.addEventListener('keydown', function (event) {
        if (event.key === '/' && document.activeElement !== filter) {
          event.preventDefault();
          filter.focus();
        }
      });
    }

    // 2) 复制当前文件路径（项目相对路径）
    var copy = document.getElementById('pvCopy');
    if (copy) {
      copy.addEventListener('click', function () {
        var code = document.querySelector('.pv-code');
        var path = code ? code.getAttribute('data-path') : '';
        if (!path) {
          var title = document.querySelector('.pv-file-title h2');
          path = title ? title.textContent.trim() : '';
        }
        navigator.clipboard.writeText(path).then(function () {
          var old = copy.textContent;
          copy.textContent = '已复制';
          setTimeout(function () { copy.textContent = old; }, 1500);
        });
      });
    }

    // 3) 目录树里当前节点滚动到可视区域
    var active = document.querySelector('.pv-leaf.on');
    if (active && active.scrollIntoView) {
      active.scrollIntoView({ block: 'nearest' });
    }

    // 4) 代码行太长时，双击行可横向滚动到头
    var codeBox = document.querySelector('.pv-code');
    if (codeBox) {
      codeBox.addEventListener('dblclick', function () {
        codeBox.scrollLeft = codeBox.scrollLeft > 0 ? 0 : codeBox.scrollWidth;
      });
    }
  });
})();
