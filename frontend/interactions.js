(() => {
  let toast, toastTimer, actionContext = null;
  let actionSequence = 0;
  let overlay, overlayShownAt = 0, overlayShowTimer, overlayHideTimer, routeCycle = null, actionCycle = null;
  const overlayOwners = new Set();
  let initialLoadToken = {};
  let initialLoadTimer = null;

  function ensureOverlay() {
    if (overlay?.isConnected) return overlay;
    overlay = document.querySelector('#sli-initial-loader');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'sli-page-loading';
    overlay.hidden = true;
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = '<div class="sli-page-loading-card"><div class="sli-loader-mark" aria-hidden="true"><i></i><i></i><i></i></div><strong>Đang tải dữ liệu</strong><span>Đang đồng bộ các thành phần của trang…</span></div>';
    document.body.appendChild(overlay);
    return overlay;
  }

  function releaseInitialLoader() {
    if (!initialLoadToken) return;
    clearTimeout(initialLoadTimer);
    releaseOverlay(initialLoadToken);
    initialLoadToken = null;
  }

  overlayOwners.add(initialLoadToken);
  overlay = ensureOverlay();
  overlay.hidden = false;
  overlay.classList.add('is-visible');
  overlayShownAt = Date.now();
  initialLoadTimer = setTimeout(() => {
    releaseInitialLoader();
    announce('Một số dữ liệu phản hồi chậm; đang mở các phần đã tải được.', true);
  }, 10000);

  function acquireOverlay() {
    const token = {};
    overlayOwners.add(token);
    clearTimeout(overlayHideTimer);
    const node = ensureOverlay();
    if (node.hidden && !overlayShowTimer) overlayShowTimer = setTimeout(() => {
      overlayShowTimer = null;
      if (!overlayOwners.size) return;
      node.hidden = false;
      requestAnimationFrame(() => node.classList.add('is-visible'));
      overlayShownAt = Date.now();
    }, 90);
    return token;
  }

  function releaseOverlay(token) {
    if (token) overlayOwners.delete(token);
    if (overlayOwners.size) return;
    clearTimeout(overlayShowTimer);
    overlayShowTimer = null;
    const delay = Math.max(0, 380 - (Date.now() - overlayShownAt));
    clearTimeout(overlayHideTimer);
    overlayHideTimer = setTimeout(() => {
      if (overlayOwners.size || !overlay) return;
      overlay.classList.remove('is-visible');
      setTimeout(() => { if (!overlayOwners.size && overlay) overlay.hidden = true; }, 180);
    }, delay);
  }

  function makeCycle(kind) {
    return { kind, at: Date.now(), lastActivity: Date.now(), pending: 0, settled: kind === 'action', token: null, endTimer: null, hardTimer: null };
  }

  function armCycleLimit(cycle) {
    clearTimeout(cycle.hardTimer);
    cycle.hardTimer = setTimeout(() => {
      if (!cycle.token) return;
      const token = cycle.token;
      cycle.token = null;
      cycle.pending = 0;
      cycle.settled = true;
      releaseOverlay(token);
      if (routeCycle === cycle) routeCycle = null;
      if (actionCycle === cycle) actionCycle = null;
      announce('Một số dữ liệu phản hồi chậm; đang mở các phần đã tải được.', true);
    }, cycle.kind === 'route' ? 10000 : 18000);
  }

  function trackCycle(cycle) {
    if (!cycle) return;
    cycle.pending += 1;
    cycle.lastActivity = Date.now();
    clearTimeout(cycle.endTimer);
    if (!cycle.token) { cycle.token = acquireOverlay(); armCycleLimit(cycle); }
  }

  function finishCycleRequest(cycle) {
    if (!cycle) return;
    cycle.pending = Math.max(0, cycle.pending - 1);
    cycle.lastActivity = Date.now();
    finishCycleIfIdle(cycle);
  }

  function finishCycleIfIdle(cycle) {
    if (!cycle || cycle.pending || !cycle.settled) return;
    cycle.endTimer = setTimeout(() => {
      if (cycle.pending) return;
      releaseOverlay(cycle.token);
      cycle.token = null;
      clearTimeout(cycle.hardTimer);
      if (routeCycle === cycle) routeCycle = null;
      if (actionCycle === cycle) actionCycle = null;
    }, 520);
  }

  document.addEventListener('sli-route-start', event => {
    if (routeCycle) { routeCycle.settled = true; finishCycleIfIdle(routeCycle); }
    const cycle = makeCycle('route');
    cycle.token = acquireOverlay();
    armCycleLimit(cycle);
    routeCycle = cycle;
  });

  document.addEventListener('sli-route-loading', event => {
    const cycle = routeCycle && routeCycle.kind === 'route' ? routeCycle : makeCycle('route');
    if (!cycle.token) { cycle.token = acquireOverlay(); armCycleLimit(cycle); }
    routeCycle = cycle;
    Promise.resolve(event.detail?.promise).catch(() => {}).finally(() => {
      releaseInitialLoader();
      cycle.settled = true;
      cycle.lastActivity = Date.now();
      finishCycleIfIdle(cycle);
    });
  });

  function ensureToast() {
    if (toast?.isConnected) return toast;
    toast = document.createElement('div');
    toast.className = 'sli-interaction-toast';
    toast.hidden = true;
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    document.body.appendChild(toast);
    return toast;
  }

  function announce(message, error = false, pending = false) {
    const node = ensureToast();
    clearTimeout(toastTimer);
    node.textContent = message;
    node.classList.toggle('is-error', error);
    node.classList.toggle('is-pending', pending);
    node.hidden = false;
    if (!pending) toastTimer = setTimeout(() => { node.hidden = true; }, 2600);
  }

  window.SLIInteractionFeedback = { announce };

  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.getAttribute('aria-busy') === 'true') {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    button.classList.remove('interaction-pulse');
    void button.offsetWidth;
    button.classList.add('interaction-pulse');
    setTimeout(() => button.classList.remove('interaction-pulse'), 420);
    if (button.matches('[data-route], .nav-item')) { actionContext = null; return; }
    actionContext = { button, at: Date.now(), used: false, id: ++actionSequence };
    actionCycle = makeCycle('action');
  }, true);

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    let url = '';
    try { url = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.href).pathname; } catch (_) {}
    const context = url.startsWith('/api/') && actionContext && !actionContext.used && Date.now() - actionContext.at < 1100 ? actionContext : null;
    const isApi = url.startsWith('/api/');
    const now = Date.now();
    const actionWindow = actionCycle && now - actionCycle.at < 20000 && (now - actionCycle.at < 1100 || actionCycle.pending > 0 || now - actionCycle.lastActivity < 650) ? actionCycle : null;
    const routeWindow = routeCycle && now - routeCycle.at < 30000 && (!routeCycle.settled || now - routeCycle.lastActivity < 650) ? routeCycle : null;
    const cycles = isApi ? [actionWindow, routeWindow].filter(Boolean) : [];
    cycles.forEach(trackCycle);
    if (context) context.used = true;
    const button = context?.button;
    if (context && button?.isConnected) {
      button.dataset.sliActionState = 'loading';
      button.setAttribute('aria-busy', 'true');
    }
    if (context) announce('Đang xử lý thao tác…', false, true);
    if (!context && !cycles.length) return originalFetch(...args);
    let responseOk = false;
    let responseStatus = 0;
    let settled = false;
    let cyclesFinished = false;
    const finishCycles = () => { if (cyclesFinished) return; cyclesFinished = true; cycles.forEach(finishCycleRequest); };
    const timeout = setTimeout(() => {
      if (settled || !context) return;
      settled = true;
      if (button?.isConnected && button.dataset.sliActionState === 'loading') {
        delete button.dataset.sliActionState;
        button.removeAttribute('aria-busy');
      }
      announce('Tác vụ đang phản hồi chậm. Kiểm tra trạng thái trước khi bấm lại.', true);
      finishCycles();
    }, 15000);
    try {
      const response = await originalFetch(...args);
      responseOk = response.ok;
      responseStatus = response.status;
      if (!settled) {
        settled = true;
        clearTimeout(timeout);
        if (context && button?.isConnected && button.dataset.sliActionState === 'loading') {
          button.dataset.sliActionState = responseOk ? 'success' : 'error';
          button.removeAttribute('aria-busy');
          setTimeout(() => { if (button.dataset.sliActionState === (responseOk ? 'success' : 'error')) delete button.dataset.sliActionState; }, 1500);
        }
        if (context) announce(responseOk ? 'Đã nhận phản hồi từ máy chủ.' : `Thao tác chưa hoàn tất (HTTP ${responseStatus}).`, !responseOk);
      }
      return response;
    } catch (error) {
      responseOk = false;
      if (!settled && context) {
        settled = true;
        clearTimeout(timeout);
        if (button?.isConnected && button.dataset.sliActionState === 'loading') {
          button.dataset.sliActionState = 'error';
          button.removeAttribute('aria-busy');
          setTimeout(() => { if (button.dataset.sliActionState === 'error') delete button.dataset.sliActionState; }, 2000);
        }
        announce('Không kết nối được máy chủ. Thao tác chưa hoàn tất.', true);
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      finishCycles();
    }
  };
})();
