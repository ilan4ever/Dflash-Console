/** Replace native selects with themed dropdowns (no system blue hover). */
(function () {
  function closeAllMenus(except) {
    document.querySelectorAll('.df-select-menu.open').forEach((menu) => {
      if (menu !== except) {
        menu.classList.remove('open');
        menu.style.width = '';
      }
    });
    document.querySelectorAll('.df-select-trigger[aria-expanded="true"]').forEach((btn) => {
      if (!except || btn.nextElementSibling !== except) btn.setAttribute('aria-expanded', 'false');
    });
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

    select.classList.add('df-select-native');
    select.tabIndex = -1;

    function selectedLabel() {
      const opt = select.options[select.selectedIndex];
      return opt?.textContent?.trim() || 'Select…';
    }

    function syncDisabled() {
      trigger.disabled = select.disabled;
      trigger.classList.toggle('is-disabled', select.disabled);
    }

    function buildMenu() {
      menu.innerHTML = '';
      [...select.options].forEach((opt) => {
        if (opt.disabled && !opt.value) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'df-select-option';
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
          menu.classList.remove('open');
          menu.style.width = '';
          trigger.setAttribute('aria-expanded', 'false');
        });
        menu.appendChild(btn);
      });
    }

    function syncTrigger() {
      trigger.textContent = selectedLabel();
      syncDisabled();
    }

    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      if (select.disabled) return;
      const willOpen = !menu.classList.contains('open');
      closeAllMenus(willOpen ? menu : null);
      if (willOpen) {
        buildMenu();
        menu.style.minWidth = `${Math.ceil(trigger.getBoundingClientRect().width)}px`;
        menu.classList.add('open');
        trigger.setAttribute('aria-expanded', 'true');
        requestAnimationFrame(() => {
          let maxWidth = trigger.getBoundingClientRect().width;
          menu.querySelectorAll('.df-select-option').forEach((item) => {
            maxWidth = Math.max(maxWidth, item.scrollWidth);
          });
          menu.style.width = `${Math.ceil(maxWidth)}px`;
        });
      } else {
        menu.classList.remove('open');
        menu.style.width = '';
        trigger.setAttribute('aria-expanded', 'false');
      }
    });

    select.addEventListener('change', syncTrigger);
    new MutationObserver(syncTrigger).observe(select, { attributes: true, attributeFilter: ['disabled'] });
    new MutationObserver(() => {
      if (menu.classList.contains('open')) buildMenu();
      syncTrigger();
    }).observe(select, { childList: true, subtree: true, characterData: true });

    wrap.appendChild(trigger);
    wrap.appendChild(menu);
    syncTrigger();
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

  window.DFlashSelectTheme = { enhanceAll, enhanceSelect };
})();
