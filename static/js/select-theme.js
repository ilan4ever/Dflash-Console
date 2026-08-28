/** Replace native selects with themed dropdowns (no system blue hover). */
(function () {
  const REBUILD_DEBOUNCE_MS = 80;

  function closeAllMenus(except) {
    document.querySelectorAll('.df-select-menu.open').forEach((menu) => {
      if (menu !== except) {
        menu.classList.remove('open');
        menu.style.position = '';
        menu.style.left = '';
        menu.style.top = '';
        menu.style.right = '';
        menu.style.bottom = '';
        menu.style.maxHeight = '';
        menu.style.maxWidth = '';
        menu.style.width = '';
        menu.style.minWidth = '';
        const input = menu.querySelector('.df-select-filter');
        if (input) input.value = '';
      }
    });
    document.querySelectorAll('.df-select-trigger[aria-expanded="true"]').forEach((btn) => {
      if (!except || btn.nextElementSibling !== except) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function normalizeFilter(value) {
    return String(value || '').trim().toLowerCase();
  }

  function collapseRepeats(value) {
    return String(value || '').replace(/(.)\1+/g, '$1');
  }

  function matchesFilter(text, query) {
    if (!query) return true;
    const normalized = normalizeFilter(text);
    if (normalized.includes(query)) return true;
    const collapsedText = collapseRepeats(normalized);
    const collapsedQuery = collapseRepeats(query);
    return collapsedText.includes(collapsedQuery);
  }

  function isModelFilterSelect(select) {
    if (!(select instanceof HTMLSelectElement)) return false;
    if (select.dataset.dfModelFilter === '1') return true;
    if (select.dataset.dfModelFilter === '0') return false;
    const id = select.id || '';
    if (/ModelPick|CheckpointPick|TargetPick$/i.test(id)) return true;
    if (select.classList.contains('lm-engine-model-pick') || select.classList.contains('df-chat-checkpoint-pick')) {
      return true;
    }
    if (select.dataset.rtField === 'default_model') return true;
    return false;
  }

  function isMenuOpen(select) {
    if (!(select instanceof HTMLSelectElement)) return false;
    const menu = select.closest('.df-select-wrap')?.querySelector('.df-select-menu');
    return menu?.classList.contains('open') === true;
  }

  function enhanceSelect(select) {
    if (!(select instanceof HTMLSelectElement)) return;
    if (select.dataset.dfEnhanced === '1') return;
    if (select.multiple || select.size > 1) return;
    if (select.closest('.df-select-wrap')) return;

    select.dataset.dfEnhanced = '1';

    const wrap = document.createElement('div');
    wrap.className = 'df-select-wrap';
    if (select.classList.contains('full')) wrap.classList.add('full');
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = [...select.classList].filter((c) => c !== 'df-select-native').join(' ') + ' df-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    const menu = document.createElement('div');
    menu.className = 'df-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.addEventListener('mousedown', (e) => e.stopPropagation());
    menu.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true });

    const searchable = isModelFilterSelect(select);
    if (searchable) menu.classList.add('has-filter');

    select.classList.add('df-select-native');
    select.tabIndex = -1;

    let filterInput = null;

    function selectedLabel() {
      const opt = select.options[select.selectedIndex];
      return opt?.textContent?.trim() || 'Select…';
    }

    function syncDisabled() {
      trigger.disabled = select.disabled;
      trigger.classList.toggle('is-disabled', select.disabled);
    }

    function syncTriggerClasses() {
      const selectClasses = [...select.classList].filter((c) => c !== 'df-select-native');
      trigger.className = `${selectClasses.join(' ')} df-select-trigger`.trim();
      syncDisabled();
    }

    function clearMenuPosition() {
      menu.style.position = '';
      menu.style.left = '';
      menu.style.top = '';
      menu.style.right = '';
      menu.style.bottom = '';
      menu.style.maxHeight = '';
      menu.style.maxWidth = '';
      menu.style.width = '';
      menu.style.minWidth = '';
    }

    function positionOpenMenu() {
      const margin = 8;
      const rect = trigger.getBoundingClientRect();
      const maxPreferred = searchable
        ? Math.min(320, window.innerHeight * 0.55)
        : Math.min(280, window.innerHeight * 0.5);
      const spaceBelow = window.innerHeight - rect.bottom - margin;
      const spaceAbove = rect.top - margin;
      const openBelow = spaceBelow >= 160 || spaceBelow >= spaceAbove;
      const available = Math.max(120, openBelow ? spaceBelow - 4 : spaceAbove - 4);
      const height = Math.min(maxPreferred, available);

      const widthCap = Math.min(searchable ? 520 : 420, window.innerWidth - margin * 2);
      let menuWidth = Math.ceil(rect.width);
      if (!searchable) {
        menu.querySelectorAll('.df-select-option').forEach((item) => {
          menuWidth = Math.max(menuWidth, Math.min(item.scrollWidth, widthCap));
        });
      }
      if (searchable) menuWidth = Math.max(menuWidth, 420);
      menuWidth = Math.min(Math.max(menuWidth, Math.ceil(rect.width)), widthCap);

      let left = rect.left;
      if (left + menuWidth > window.innerWidth - margin) {
        left = Math.max(margin, window.innerWidth - margin - menuWidth);
      }

      menu.style.position = 'fixed';
      menu.style.left = `${Math.round(left)}px`;
      menu.style.width = `${Math.ceil(menuWidth)}px`;
      menu.style.minWidth = `${Math.ceil(menuWidth)}px`;
      menu.style.maxWidth = `${widthCap}px`;
      menu.style.maxHeight = `${Math.floor(height)}px`;
      menu.style.top = openBelow
        ? `${Math.round(rect.bottom + 4)}px`
        : `${Math.round(rect.top - height - 4)}px`;
    }

    function closeMenu() {
      menu.classList.remove('open');
      clearMenuPosition();
      trigger.setAttribute('aria-expanded', 'false');
      if (filterInput) filterInput.value = '';
    }

    function createOptionButton(opt) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'df-select-option' + (opt.className ? ` ${opt.className}` : '');
      btn.setAttribute('role', 'option');
      btn.textContent = opt.textContent;
      btn.dataset.value = opt.value;
      if (opt.selected) {
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
      }
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        select.value = opt.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        select.dispatchEvent(new Event('input', { bubbles: true }));
        trigger.textContent = selectedLabel();
        menu.querySelectorAll('.df-select-option').forEach((item) => {
          const active = item.dataset.value === opt.value;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        closeMenu();
      });
      return btn;
    }

    function applyFilter() {
      const listRoot = menu.querySelector('.df-select-options');
      if (!listRoot) return;
      const query = normalizeFilter(filterInput?.value);
      let totalVisible = 0;

      listRoot.querySelectorAll('.df-select-group').forEach((group) => {
        let groupVisible = 0;
        group.querySelectorAll('.df-select-option').forEach((item) => {
          const match = matchesFilter(item.textContent, query);
          item.hidden = !match;
          item.classList.toggle('is-filter-hidden', !match);
          if (match) groupVisible += 1;
        });
        const label = group.querySelector('.df-select-group-label');
        if (label) label.hidden = groupVisible === 0;
        totalVisible += groupVisible;
      });

      listRoot.querySelectorAll(':scope > .df-select-option').forEach((item) => {
        const match = matchesFilter(item.textContent, query);
        item.hidden = !match;
        item.classList.toggle('is-filter-hidden', !match);
        if (match) totalVisible += 1;
      });

      let emptyEl = listRoot.querySelector('.df-select-filter-empty');
      if (query && totalVisible === 0) {
        if (!emptyEl) {
          emptyEl = document.createElement('div');
          emptyEl.className = 'df-select-filter-empty';
          emptyEl.textContent = 'No matching models';
          listRoot.appendChild(emptyEl);
        }
        emptyEl.hidden = false;
      } else if (emptyEl) {
        emptyEl.hidden = true;
      }
    }

    function buildMenu() {
      const prevFilter = filterInput?.value || '';
      menu.innerHTML = '';

      if (searchable) {
        const filterWrap = document.createElement('div');
        filterWrap.className = 'df-select-filter-wrap';
        filterInput = document.createElement('input');
        filterInput.type = 'search';
        filterInput.className = 'df-select-filter';
        filterInput.placeholder = 'Filter models…';
        filterInput.setAttribute('aria-label', 'Filter models');
        filterInput.addEventListener('input', () => applyFilter());
        filterInput.addEventListener('click', (e) => e.stopPropagation());
        filterInput.addEventListener('keydown', (e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            if (filterInput.value) {
              filterInput.value = '';
              applyFilter();
            } else {
              closeMenu();
            }
          }
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            const first = menu.querySelector('.df-select-option:not([hidden])');
            first?.focus();
          }
        });
        filterWrap.appendChild(filterInput);
        menu.appendChild(filterWrap);
      } else {
        filterInput = null;
      }

      const listRoot = document.createElement('div');
      listRoot.className = 'df-select-options';
      menu.appendChild(listRoot);

      [...select.children].forEach((child) => {
        let parent = listRoot;
        if (child instanceof HTMLOptGroupElement) {
          const group = document.createElement('div');
          group.className = 'df-select-group';
          const heading = document.createElement('div');
          heading.className = 'df-select-group-label';
          heading.textContent = child.label;
          group.appendChild(heading);
          listRoot.appendChild(group);
          parent = group;
        }
        const options = child instanceof HTMLOptGroupElement ? [...child.children] : [child];
        options.forEach((opt) => {
          if (!(opt instanceof HTMLOptionElement)) return;
          if (opt.disabled && !opt.value) return;
          parent.appendChild(createOptionButton(opt));
        });
      });

      if (filterInput && prevFilter) {
        filterInput.value = prevFilter;
      }
      if (searchable) applyFilter();
    }

    function syncTrigger() {
      trigger.textContent = selectedLabel();
      syncDisabled();
      if (select.disabled && menu.classList.contains('open')) closeMenu();
    }

    function openMenu() {
      buildMenu();
      menu.classList.add('open');
      trigger.setAttribute('aria-expanded', 'true');
      requestAnimationFrame(() => {
        positionOpenMenu();
        if (filterInput) filterInput.focus();
      });
    }

    let rebuildTimer = 0;
    function scheduleMenuRebuild() {
      if (!menu.classList.contains('open') || select.disabled) return;
      window.clearTimeout(rebuildTimer);
      rebuildTimer = window.setTimeout(() => {
        rebuildTimer = 0;
        if (!menu.classList.contains('open')) return;
        const keepFilter = filterInput?.value || '';
        buildMenu();
        if (keepFilter && filterInput) {
          filterInput.value = keepFilter;
          applyFilter();
        }
      }, REBUILD_DEBOUNCE_MS);
    }

    if (!menu.dataset.dfPositionBound) {
      menu.dataset.dfPositionBound = '1';
      const repositionOpenMenu = () => {
        if (!menu.classList.contains('open')) return;
        positionOpenMenu();
      };
      window.addEventListener('resize', repositionOpenMenu);
      window.addEventListener('scroll', repositionOpenMenu, true);
    }

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      if (select.disabled) return;
      const willOpen = !menu.classList.contains('open');
      closeAllMenus(willOpen ? menu : null);
      if (willOpen) openMenu();
      else closeMenu();
    });

    select.addEventListener('change', syncTrigger);
    new MutationObserver((mutations) => {
      syncTriggerClasses();
      const optionsChanged = mutations.some((m) => m.type === 'childList');
      if (optionsChanged) scheduleMenuRebuild();
      syncTrigger();
    }).observe(select, { attributes: true, attributeFilter: ['disabled', 'class'], childList: true, subtree: true });

    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    syncTrigger();
  }

  function syncSelect(select) {
    if (!(select instanceof HTMLSelectElement)) return;
    const wrap = select.closest('.df-select-wrap');
    const trigger = wrap?.querySelector('.df-select-trigger');
    if (!trigger) return;
    const selectClasses = [...select.classList].filter((c) => c !== 'df-select-native');
    trigger.className = `${selectClasses.join(' ')} df-select-trigger`.trim();
    trigger.disabled = select.disabled;
    trigger.classList.toggle('is-disabled', select.disabled);
    const opt = select.options[select.selectedIndex];
    trigger.textContent = opt?.textContent?.trim() || 'Select…';
  }

  function enhanceAll(root) {
    const scope = root || document;
    scope.querySelectorAll('.df-shell select:not([data-df-enhanced-skip])').forEach(enhanceSelect);
  }

  document.addEventListener('click', () => closeAllMenus());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllMenus();
  });

  document.addEventListener('DOMContentLoaded', () => enhanceAll());

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.matches?.('select:not([data-df-enhanced-skip])')) enhanceSelect(node);
        node.querySelectorAll?.('select:not([data-df-enhanced-skip])')?.forEach(enhanceSelect);
      });
    });
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  window.DFlashSelectTheme = {
    enhanceAll,
    enhanceSelect,
    syncSelect,
    isModelFilterSelect,
    isMenuOpen,
    closeAllMenus,
  };
})();
