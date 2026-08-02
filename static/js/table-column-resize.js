/** Resizable table columns — widths persist in config.json */
(function () {
  const layout = () => window.DFlashUiLayout;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function colMinWidth(th) {
    const raw = parseInt(th.dataset.colMin || '', 10);
    if (Number.isFinite(raw)) return raw;
    if (th.classList.contains('lm-col-action')) return 56;
    if (th.classList.contains('lm-col-model')) return 180;
    return 44;
  }

  function loadWidths(storageKey, headers) {
    const saved = layout()?.getTableColumns?.(storageKey);
    if (!saved) return null;
    return headers.map((th, index) => {
      const id = th.dataset.colId || String(index);
      const value = saved[id];
      return Number.isFinite(value) && value > 0 ? value : null;
    });
  }

  function saveWidths(storageKey, headers, widths) {
    const payload = {};
    headers.forEach((th, index) => {
      const id = th.dataset.colId || String(index);
      payload[id] = Math.round(widths[index]);
    });
    layout()?.setTableColumns?.(storageKey, payload);
  }

  function applyWidths(colEls, widths) {
    colEls.forEach((col, index) => {
      col.style.width = `${Math.round(widths[index])}px`;
    });
  }

  function defaultWidthFor(th) {
    const raw = parseInt(th.dataset.colDefault || '', 10);
    if (Number.isFinite(raw)) return raw;
    if (th.classList.contains('lm-col-model')) return 420;
    if (th.classList.contains('lm-col-action')) return 72;
    if (th.dataset.colId === 'source') return 88;
    if (th.dataset.colId === 'updated') return 88;
    return 64;
  }

  function widthsUsable(widths, headers) {
    if (!widths || widths.length !== headers.length) return false;
    return widths.every((value, index) => (
      Number.isFinite(value)
      && value >= colMinWidth(headers[index])
    ));
  }

  function queueRemeasure(table, headers, colEls, storageKey) {
    if (table.dataset.colsMeasured === '1') return;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      requestAnimationFrame(() => {
        if (table.getBoundingClientRect().width <= 0) return;
        if (layout()?.getTableColumns?.(storageKey)) {
          table.dataset.colsMeasured = '1';
          return;
        }
        const widths = measureWidths(table, headers);
        applyWidths(colEls, widths);
        table.dataset.colsMeasured = '1';
      });
    }, { threshold: 0.01 });
    observer.observe(table);
  }

  function measureWidths(table, headers) {
    const prevLayout = table.style.tableLayout;
    table.style.tableLayout = 'auto';
    const widths = headers.map((th) => th.getBoundingClientRect().width);
    table.style.tableLayout = prevLayout;
    return widths;
  }

  function rightColumnIndices(index, length) {
    const indices = [];
    for (let i = index + 1; i < length; i += 1) indices.push(i);
    return indices;
  }

  function totalSlack(widths, indices, headers) {
    return indices.reduce((sum, colIndex) => (
      sum + Math.max(0, widths[colIndex] - colMinWidth(headers[colIndex]))
    ), 0);
  }

  function applyBoundaryResize(widths, startWidths, headers, leftIndex, delta) {
    const rightIndices = rightColumnIndices(leftIndex, headers.length);
    const next = startWidths.slice();
    const leftMin = colMinWidth(headers[leftIndex]);
    const desiredLeft = startWidths[leftIndex] + delta;

    if (rightIndices.length === 0) {
      next[leftIndex] = Math.max(leftMin, desiredLeft);
      return next;
    }

    if (desiredLeft >= startWidths[leftIndex]) {
      const growBy = desiredLeft - startWidths[leftIndex];
      const slack = totalSlack(startWidths, rightIndices, headers);
      const applied = Math.min(growBy, slack);
      next[leftIndex] = startWidths[leftIndex] + applied;
      if (applied > 0) {
        rightIndices.forEach((colIndex) => {
          const colSlack = Math.max(0, startWidths[colIndex] - colMinWidth(headers[colIndex]));
          const share = slack > 0 ? (colSlack / slack) * applied : 0;
          next[colIndex] = startWidths[colIndex] - share;
        });
      }
      return next;
    }

    const shrinkLeftBy = startWidths[leftIndex] - Math.max(leftMin, desiredLeft);
    next[leftIndex] = startWidths[leftIndex] - shrinkLeftBy;
    if (shrinkLeftBy > 0) {
      const rightTotal = rightIndices.reduce((sum, colIndex) => sum + startWidths[colIndex], 0);
      rightIndices.forEach((colIndex) => {
        const share = rightTotal > 0
          ? (startWidths[colIndex] / rightTotal) * shrinkLeftBy
          : shrinkLeftBy / rightIndices.length;
        next[colIndex] = startWidths[colIndex] + share;
      });
    }
    return next;
  }

  function initTable(table) {
    if (!table || table.dataset.colsReady === '1') return;

    const headers = Array.from(table.querySelectorAll('thead th'));
    if (headers.length < 2) return;

    const storageKey = table.dataset.resizeKey || table.id || 'table';
    table.classList.add('lm-resizable-table');

    let colgroup = table.querySelector('colgroup');
    if (!colgroup) {
      colgroup = document.createElement('colgroup');
      headers.forEach(() => colgroup.appendChild(document.createElement('col')));
      table.insertBefore(colgroup, table.firstChild);
    }
    const colEls = Array.from(colgroup.children);

    let widths = loadWidths(storageKey, headers);
    if (!widthsUsable(widths, headers)) {
      if (table.getBoundingClientRect().width > 0) {
        widths = measureWidths(table, headers);
      } else {
        widths = headers.map((th) => defaultWidthFor(th));
        queueRemeasure(table, headers, colEls, storageKey);
      }
    }
    applyWidths(colEls, widths);

    headers.forEach((th, index) => {
      if (index >= headers.length - 1) return;

      if (th.querySelector('.lm-col-resize-handle')) return;

      const handle = document.createElement('span');
      handle.className = 'lm-col-resize-handle';
      handle.setAttribute('role', 'separator');
      handle.setAttribute('aria-orientation', 'vertical');
      handle.setAttribute('aria-label', `Resize ${th.textContent.trim() || 'column'} column`);
      handle.tabIndex = 0;
      th.appendChild(handle);

      const startResize = (clientX) => {
        const startX = clientX;
        const startWidths = widths.slice();
        document.body.classList.add('lm-resizing-table-col');
        handle.classList.add('active');

        const onMove = (ev) => {
          const next = applyBoundaryResize(widths, startWidths, headers, index, ev.clientX - startX);
          next.forEach((value, colIndex) => {
            widths[colIndex] = value;
          });
          applyWidths(colEls, widths);
        };

        const onUp = () => {
          document.body.classList.remove('lm-resizing-table-col');
          handle.classList.remove('active');
          window.removeEventListener('mousemove', onMove);
          window.removeEventListener('mouseup', onUp);
          saveWidths(storageKey, headers, widths);
        };

        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      };

      handle.addEventListener('mousedown', (event) => {
        event.preventDefault();
        event.stopPropagation();
        startResize(event.clientX);
      });

      handle.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const delta = event.key === 'ArrowRight' ? 12 : -12;
        const next = applyBoundaryResize(widths, widths.slice(), headers, index, delta);
        next.forEach((value, colIndex) => {
          widths[colIndex] = value;
        });
        applyWidths(colEls, widths);
        saveWidths(storageKey, headers, widths);
      });
    });

    table.dataset.colsReady = '1';
  }

  function initAll(root = document) {
    root.querySelectorAll('[data-resizable-columns]').forEach(initTable);
  }

  function boot() {
    const ready = layout()?.whenReady?.() ?? Promise.resolve();
    ready.then(() => initAll());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.DFlashTableColumnResize = { init: initTable, initAll };
})();
