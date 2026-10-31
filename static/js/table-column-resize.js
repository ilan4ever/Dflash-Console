/** Resizable table columns — widths persist in config.json */
(function () {
  const layout = () => window.DFlashUiLayout;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function colMinWidth(th) {
    const raw = parseInt(th.dataset.colMin || '', 10);
    if (Number.isFinite(raw)) return raw;
    if (th.classList.contains('lm-col-model')) return 300;
    if (th.classList.contains('lm-col-action')) return 132;
    if (th.dataset.colId === 'source') return 48;
    if (th.dataset.colId === 'updated') return 96;
    return 36;
  }

  function resolveWidths(headers, saved) {
    return headers.map((th) => {
      const id = th.dataset.colId || '';
      const value = saved?.[id];
      const min = colMinWidth(th);
      if (Number.isFinite(value) && value >= min) return value;
      return defaultWidthFor(th);
    });
  }

  function loadWidths(storageKey, headers) {
    const saved = layout()?.getTableColumns?.(storageKey);
    if (!saved || typeof saved !== 'object') return null;
    return resolveWidths(headers, saved);
  }

  function saveWidths(storageKey, headers, widths) {
    const payload = {};
    headers.forEach((th, index) => {
      const id = th.dataset.colId || String(index);
      payload[id] = Math.round(widths[index]);
    });
    const ui = layout();
    ui?.setTableColumns?.(storageKey, payload);
    void ui?.flush?.();
  }

  const ACTION_COL_MIN_PX = 280;
  const MODEL_ACTION_FLOOR_PX = 248;
  const META_SQUEEZE = {
    family: 48,
    scale: 36,
    lab: 52,
    source: 48,
    disk: 42,
    updated: 64,
    slots: 40,
  };

  function metaSqueeze(header) {
    const id = header?.dataset?.colId || '';
    return META_SQUEEZE[id] ?? colMinWidth(header);
  }

  function wrapNameCut(text, firstWidth, source) {
    const probe = wrapNameCut.probe || (wrapNameCut.probe = document.createElement('span'));
    if (!probe.isConnected) {
      probe.setAttribute('aria-hidden', 'true');
      probe.style.cssText = 'position:absolute;left:0;top:0;visibility:hidden;white-space:nowrap;pointer-events:none;';
      document.body.appendChild(probe);
    }
    const cs = source ? getComputedStyle(source) : null;
    probe.style.font = cs?.font || '';
    let lo = 0;
    let hi = text.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      probe.textContent = text.slice(0, mid);
      if (probe.getBoundingClientRect().width <= firstWidth) lo = mid;
      else hi = mid - 1;
    }
    if (lo >= text.length) return text.length;
    const start = Math.max(0, lo - 14);
    const slice = text.slice(start, lo);
    const rel = Math.max(slice.lastIndexOf('-'), slice.lastIndexOf('_'), slice.lastIndexOf(' '));
    return rel >= 0 ? start + rel + 1 : Math.max(1, lo);
  }

  function namesOnScreen(table) {
    const wrap = table.parentElement;
    if (!wrap) return [];
    const view = wrap.getBoundingClientRect();
    if (view.height < 40) return [];
    const headerBottom = table.querySelector('thead')?.getBoundingClientRect().bottom ?? view.top;
    const top = Math.max(view.top, headerBottom);
    const bottom = view.bottom;
    const visible = [];
    table.querySelectorAll('tr.lm-library-card-line1 .lm-model-title-text').forEach((name) => {
      const rect = name.getBoundingClientRect();
      if (rect.height < 1) return;
      if (rect.bottom > top + 1 && rect.top < bottom - 1) visible.push(name);
    });
    return visible;
  }

  function measureVisibleModelName(table) {
    const names = namesOnScreen(table);
    if (!names.length) return 0;
    const sampleCell = names[0].closest('td');
    const cs = sampleCell ? getComputedStyle(sampleCell) : null;
    const pad = (parseFloat(cs?.paddingLeft) || 0) + (parseFloat(cs?.paddingRight) || 0) + 8;
    let max = 0;
    names.forEach((name) => {
      const title = name.closest('.lm-library-handle-title');
      const pin = title?.querySelector('.lm-model-pin');
      const pinWidth = pin ? pin.offsetWidth + 6 : 0;
      max = Math.max(max, name.scrollWidth + pinWidth);
    });
    return max > 0 ? Math.ceil(max + pad) : 0;
  }

  function syncWrappedModelNames(table) {
    if (!table || table.dataset.resizeKey !== 'models-library') return;
    const span = table.querySelector('tr.lm-library-card-line2 td.lm-library-card-span');
    table.querySelectorAll('tr.lm-library-card-line1').forEach((row) => {
      const name = row.querySelector('.lm-model-title-text');
      const key = row.dataset.modelKey || '';
      const line2 = key
        ? table.querySelector(`tr.lm-library-card-line2[data-model-key="${CSS.escape(key)}"]`)
        : null;
      const line3 = key
        ? table.querySelector(`tr.lm-library-card-line3[data-model-key="${CSS.escape(key)}"]`)
        : null;
      const overflows = Boolean(name) && name.scrollWidth > name.clientWidth + 48;
      const wrap = Boolean(overflows && line2);
      row.classList.toggle('is-name-wrapped', wrap);
      line2?.classList.toggle('is-name-wrapped', wrap);
      line3?.classList.toggle('is-name-wrapped', wrap);
      const cell = line2?.querySelector('td.lm-library-card-span');
      if (!cell) return;
      let cover = cell.querySelector('.lm-model-name-cover');
      if (!wrap) {
        cover?.remove();
        return;
      }
      if (!cover) {
        cover = document.createElement('div');
        cover.className = 'lm-model-name-cover';
        cell.prepend(cover);
      }
      const text = name.textContent || '';
      cover.title = text;
      const nameRect = name.getBoundingClientRect();
      const cellRect = cell.getBoundingClientRect();
      const left = Math.max(0, nameRect.left - cellRect.left);
      const firstWidth = Math.max(40, name.clientWidth);
      const secondWidth = Math.max(firstWidth, cellRect.width - left - 8);
      const cut = wrapNameCut(text, firstWidth, name);
      const headText = text.slice(0, cut);
      const tailText = text.slice(cut).replace(/^[-_\s]+/, '');
      cover.replaceChildren();
      const first = document.createElement('div');
      first.className = 'lm-model-name-cover-line';
      first.textContent = headText;
      first.style.maxWidth = `${firstWidth}px`;
      cover.appendChild(first);
      if (tailText) {
        const second = document.createElement('div');
        second.className = 'lm-model-name-cover-line';
        second.textContent = tailText;
        second.style.maxWidth = `${secondWidth}px`;
        cover.appendChild(second);
      }
      cover.style.top = `${nameRect.top - cellRect.top}px`;
      cover.style.left = `${left}px`;
      cover.style.width = `${secondWidth}px`;
    });
    if (span) void span.offsetWidth;
  }

  function fitModelNameColumn(table, widths, available) {
    const headers = Array.from(table.querySelectorAll('thead th'));
    const next = widths.slice();
    const actionIndex = next.length - 1;
    const modelFloor = colMinWidth(headers[0]);
    const measured = measureVisibleModelName(table);
    const modelCap = measured > 0
      ? Math.max(modelFloor, measured)
      : Math.max(modelFloor, Math.min(next[0] || defaultWidthFor(headers[0]), 280));
    const actionFloor = Math.max(
      MODEL_ACTION_FLOOR_PX,
      colMinWidth(headers[actionIndex]) || 132,
    );

    for (let i = 1; i < actionIndex; i += 1) {
      const min = colMinWidth(headers[i]);
      next[i] = Math.max(min, Number.isFinite(next[i]) ? next[i] : defaultWidthFor(headers[i]));
    }

    const metaSum = () => next.slice(1, actionIndex).reduce((sum, value) => sum + value, 0);
    const squeezeSum = () => headers.slice(1, actionIndex).reduce((sum, header) => sum + metaSqueeze(header), 0);
    let model = Math.min(modelCap, Math.max(96, available - squeezeSum() - actionFloor));
    let action = available - model - metaSum();

    if (action < actionFloor) {
      let need = actionFloor - action;
      for (let i = actionIndex - 1; i >= 1 && need > 0.5; i -= 1) {
        const floor = metaSqueeze(headers[i]);
        const spare = Math.max(0, next[i] - floor);
        const take = Math.min(spare, need);
        next[i] -= take;
        need -= take;
      }
      action = available - model - metaSum();
      if (action < actionFloor) {
        const room = available - metaSum() - actionFloor;
        model = Math.max(96, Math.min(model, room));
        action = actionFloor;
      }
    }

    next[0] = model;
    const used = next.slice(0, actionIndex).reduce((sum, value) => sum + value, 0);
    if (used + actionFloor > available + 0.5) {
      next[actionIndex] = actionFloor;
    } else {
      next[actionIndex] = Math.max(actionFloor, available - used);
    }
    return next;
  }

  function tableAvailableWidth(table) {
    const wrap = table?.parentElement;
    if (!wrap) return table?.getBoundingClientRect().width || 0;
    const cs = getComputedStyle(wrap);
    const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    return Math.max(0, wrap.clientWidth - pad);
  }

  function fitActionColumn(table, widths) {
    const next = widths.slice();
    if (!table || next.length < 2) return next;
    const available = tableAvailableWidth(table);
    if (table.dataset.resizeKey === 'models-library') {
      if (!(available > 80)) return next;
      return fitModelNameColumn(table, next, available);
    }
    if (!(available > 480)) return next;
    const total = next.reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0) || 1;
    const actionIndex = next.length - 1;
    const actionPx = (next[actionIndex] / total) * available;
    if (actionPx < ACTION_COL_MIN_PX) {
      const scale = total / available;
      const maxAction = available * 0.48 * scale;
      let need = Math.min((ACTION_COL_MIN_PX - actionPx) * scale, Math.max(0, maxAction - next[actionIndex]));
      for (let i = 0; i < actionIndex && need > 0.5; i += 1) {
        const floor = colMinWidth(table.querySelectorAll('thead th')[i]) * scale;
        const spare = Math.max(0, next[i] - floor);
        const take = Math.min(spare, need);
        next[i] -= take;
        next[actionIndex] += take;
        need -= take;
      }
    } else if (total > available + 0.5) {
      // The model column keeps its full minimum width. Give the action column
      // only what is left, down to its own minimum; if that still does not fit,
      // the wrapper scrolls horizontally instead of compressing a model name.
      const otherTotal = total - next[actionIndex];
      const targetAction = Math.max(ACTION_COL_MIN_PX, available - otherTotal);
      if (targetAction < next[actionIndex]) next[actionIndex] = targetAction;
    }
    let fittedTotal = next.reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
    if (fittedTotal > available + 0.5) {
      let excess = fittedTotal - available;
      const actionHeader = table.querySelectorAll('thead th')[actionIndex];
      const actionFloor = Math.max(132, colMinWidth(actionHeader));
      const actionSpare = Math.max(0, next[actionIndex] - actionFloor);
      const takeAction = Math.min(actionSpare, excess);
      next[actionIndex] -= takeAction;
      excess -= takeAction;

      // If the action column is already at its floor, trim the model only
      // down to its configured minimum (still enough for the longest name).
      const headers = table.querySelectorAll('thead th');
      const modelFloor = colMinWidth(headers[0]);
      const modelSpare = Math.max(0, next[0] - modelFloor);
      const takeModel = Math.min(modelSpare, excess);
      next[0] -= takeModel;
      excess -= takeModel;

      // Last, use any spare room in the metadata columns.
      for (let i = 1; i < actionIndex && excess > 0.5; i += 1) {
        const floor = colMinWidth(headers[i]);
        const spare = Math.max(0, next[i] - floor);
        const take = Math.min(spare, excess);
        next[i] -= take;
        excess -= take;
      }
    }
    return next;
  }

  function applyWidths(colEls, widths) {
    const table = colEls[0]?.closest('table');
    const fitted = fitActionColumn(table, widths);
    if (table) table._dfColWidths = widths.slice();
    const total = fitted.reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
    const base = total > 0 ? total : 1;
    const available = table ? tableAvailableWidth(table) : 0;
    const pinModelColumn = table?.dataset.resizeKey === 'models-library' && available > 80;
    if (table && pinModelColumn) {
      table.style.width = '100%';
      table.style.minWidth = '0';
      table.style.maxWidth = '100%';
      const rounded = fitted.map((value) => Math.round(Number.isFinite(value) ? value : 0));
      const others = rounded.slice(0, -1).reduce((sum, value) => sum + value, 0);
      rounded[rounded.length - 1] = Math.max(MODEL_ACTION_FLOOR_PX, available - others);
      const used = rounded.reduce((sum, value) => sum + value, 0);
      if (used > available + 1) {
        const extra = used - available;
        rounded[0] = Math.max(96, rounded[0] - extra);
        const rest = rounded.slice(0, -1).reduce((sum, value) => sum + value, 0);
        rounded[rounded.length - 1] = Math.max(MODEL_ACTION_FLOOR_PX, available - rest);
      }
      colEls.forEach((col, index) => {
        col.style.width = `${rounded[index]}px`;
        col.style.minWidth = index === rounded.length - 1 ? `${rounded[index]}px` : '';
        col.style.maxWidth = index === 0 ? `${rounded[index]}px` : '';
      });
      syncWrappedModelNames(table);
      return;
    }
    if (table) {
      // Honor the minimum widths instead of squeezing the Model name into the
      // available window. The wrapper provides a horizontal scrollbar when
      // the requested columns genuinely do not fit.
      table.style.minWidth = `${Math.ceil(total)}px`;
    }
    colEls.forEach((col, index) => {
      const value = Number.isFinite(fitted[index]) ? fitted[index] : 0;
      col.style.width = `${((value / base) * 100).toFixed(3)}%`;
      col.style.minWidth = '';
      col.style.maxWidth = '';
    });
  }

  function reflowTable(table) {
    const widths = table?._dfColWidths;
    if (!widths || !table.classList.contains('lm-resizable-table')) return;
    const colEls = Array.from(table.querySelectorAll('colgroup col'));
    if (colEls.length !== widths.length) return;
    applyWidths(colEls, widths);
  }

  function defaultWidthFor(th) {
    const raw = parseInt(th.dataset.colDefault || '', 10);
    if (Number.isFinite(raw)) return raw;
    if (th.classList.contains('lm-col-model')) return 420;
    if (th.classList.contains('lm-col-action')) return 200;
    if (th.dataset.colId === 'source') return 84;
    if (th.dataset.colId === 'updated') return 108;
    return 44;
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
    const widths = headers.map((th) => {
      const measured = Math.round(th.getBoundingClientRect().width);
      const preferred = defaultWidthFor(th);
      // Auto layout hands spare space to the widest column. That is not a real
      // content requirement, especially for Model, so do not let it inflate
      // the remembered width beyond the column's preferred width.
      return Math.max(colMinWidth(th), Math.min(measured, preferred));
    });
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

  function isCompactTableLayout() {
    if (window.matchMedia('(max-width: 899px)').matches) return true;
    const main = document.querySelector('.lm-main');
    const mainWidth = main?.getBoundingClientRect().width ?? 0;
    return mainWidth > 0 && mainWidth < 680;
  }

  function initTable(table) {
    if (!table) return;
    if (isCompactTableLayout()) {
      table.classList.remove('lm-resizable-table');
      table.dataset.colsReady = '1';
      return;
    }

    const headers = Array.from(table.querySelectorAll('thead th'));
    if (headers.length < 2) return;

    const storageKey = table.dataset.resizeKey || table.id || 'table';
    table.classList.add('lm-resizable-table');

    let colgroup = table.querySelector('colgroup');
    if (!colgroup || colgroup.children.length !== headers.length) {
      colgroup = document.createElement('colgroup');
      headers.forEach(() => colgroup.appendChild(document.createElement('col')));
      table.insertBefore(colgroup, table.firstChild);
      table.dataset.colsReady = '0';
    }
    if (table.dataset.colsReady === '1') return;

    const colEls = Array.from(colgroup.children);

    let widths = loadWidths(storageKey, headers);
    if (!widths) {
      if (table.getBoundingClientRect().width > 0) {
        widths = measureWidths(table, headers);
      } else {
        widths = headers.map((th) => defaultWidthFor(th));
        queueRemeasure(table, headers, colEls, storageKey);
      }
    }
    applyWidths(colEls, widths, headers);

    headers.forEach((th, index) => {
      if (index >= headers.length - 1) return;
      // The model column width follows the longest name, so it is not dragged.
      if (table.dataset.resizeKey === 'models-library' && index === 0) return;
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
    watchTableWidth(table);
  }

  function watchTableWidth(table) {
    const wrap = table.parentElement;
    if (!wrap || table._dfColObserver || typeof ResizeObserver === 'undefined') return;
    let last = wrap.getBoundingClientRect().width;
    const observer = new ResizeObserver(() => {
      const width = wrap.getBoundingClientRect().width;
      if (Math.abs(width - last) < 1) return;
      last = width;
      reflowTable(table);
    });
    observer.observe(wrap);
    table._dfColObserver = observer;
    if (table.dataset.resizeKey === 'models-library' && !wrap._dfNameScroll) {
      wrap._dfNameScroll = true;
      let timer = 0;
      wrap.addEventListener('scroll', () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => reflowTable(table), 90);
      }, { passive: true });
    }
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

  window.addEventListener('resize', () => {
    document.querySelectorAll('[data-resizable-columns]').forEach((table) => {
      if (isCompactTableLayout()) {
        table.classList.remove('lm-resizable-table');
        table.dataset.colsReady = '1';
        table.querySelectorAll('col').forEach((col) => {
          col.style.width = '';
        });
        return;
      }
      if (!table.classList.contains('lm-resizable-table')) {
        table.dataset.colsReady = '0';
        initTable(table);
        return;
      }
      reflowTable(table);
    });
  });

  function reflow(table) {
    if (!table) return;
    if (!table.classList.contains('lm-resizable-table') || table.dataset.colsReady !== '1') {
      table.dataset.colsReady = '0';
      initTable(table);
      return;
    }
    reflowTable(table);
  }

  window.DFlashTableColumnResize = { init: initTable, initAll, reflow };
})();
