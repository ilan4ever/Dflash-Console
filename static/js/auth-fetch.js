/**
 * Sign-in support for the browser UI (Phase 1).
 *
 * On the Console's own PC nothing changes: there is no sign-in cookie, so no
 * header is added and the Console never answers 401.
 *
 * When the owner is signed in from another computer, every POST, PUT, PATCH,
 * and DELETE to this Console carries the CSRF token from the dflash_csrf
 * cookie (the Console also checks the Origin header). When the sign-in expires
 * or is revoked, the page goes to the sign-in page.
 *
 * Loaded before every other script so all fetch() calls pass through it.
 */
(function () {
  if (window.DFlashAuthFetch || typeof window.fetch !== 'function') return;

  const nativeFetch = window.fetch.bind(window);
  const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
  let redirecting = false;

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)dflash_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function requestUrl(input) {
    if (typeof input === 'string') return input;
    if (input instanceof URL) return input.href;
    if (input && typeof input.url === 'string') return input.url;
    return String(input || '');
  }

  function sameOriginUrl(raw) {
    try {
      const url = new URL(raw, window.location.href);
      return url.origin === window.location.origin ? url : null;
    } catch (_) {
      return null;
    }
  }

  function goToSignIn() {
    if (redirecting) return;
    redirecting = true;
    const here = window.location.pathname + window.location.search + window.location.hash;
    window.location.assign(`/login?next=${encodeURIComponent(here)}`);
  }

  window.fetch = function dflashFetch(input, init) {
    const url = sameOriginUrl(requestUrl(input));
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const token = url ? csrfToken() : '';
    let options = init;
    if (token && !SAFE_METHODS.has(method)) {
      const base = (init && init.headers) || (input instanceof Request ? input.headers : undefined);
      const headers = new Headers(base);
      if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', token);
      options = { ...(init || {}), headers };
    }
    return nativeFetch(input, options).then((resp) => {
      if (
        resp.status === 401
        && url
        && url.pathname.startsWith('/api/')
        && !url.pathname.startsWith('/api/auth/')
      ) {
        goToSignIn();
      }
      return resp;
    });
  };

  window.DFlashAuthFetch = { csrfToken };
})();
