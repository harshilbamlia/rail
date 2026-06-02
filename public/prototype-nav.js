/* prototype-nav.js — stitches the nine static HTMLs into one demo app.
   Listens in BUBBLE phase so existing React onClick handlers (filters,
   tweaks, side panel, modals, print) keep working. We only navigate when
   the click is structurally identifiable — sidebar items, specific named
   anchors, or the login submit. We do not hijack row clicks.
*/
(function () {
  const NAV = {
    Queue:   'Priority Queue.html',
    Map:     'Track Map.html',
    Defect:  'Defect Detail.html',
    Cluster: 'Cluster View.html',
    Import:  'Inspection Run Import.html',
    Annex:   'Annexure III Form.html',
    Zone:    'Zone Rollup.html',
    Model:   'Model.html',
  };
  const LABELS = Object.keys(NAV);

  const PAGE = (function () {
    const file = decodeURIComponent((location.pathname.split('/').pop() || '').replace(/\?.*$/, ''));
    for (const [k, v] of Object.entries(NAV)) if (v === file) return k;
    if (/Login\.html$/i.test(file)) return 'Login';
    return null;
  })();

  const isLogin = PAGE === 'Login';

  /* ---------- Sidebar detection (structural) ---------- */
  // The sidebar is a narrow left rail (width ~44 px, full-height). Each item
  // is a small div containing an icon span + uppercase label span. We detect
  // a click as a sidebar action only when (a) the clicked element sits inside
  // a narrow vertical ancestor, and (b) its label text matches a known route.
  function findSidebarTarget(el) {
    // (a) narrow vertical ancestor check
    let inSidebar = false;
    let n = el;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      const r = n.getBoundingClientRect();
      if (r.width > 0 && r.width < 80 && r.height > 250) { inSidebar = true; break; }
    }
    if (!inSidebar) return null;
    // (b) label text match — handle icon prefix (≡Queue, ◈Map, □Annex, etc.)
    n = el;
    for (let i = 0; n && i < 4; i++, n = n.parentElement) {
      const t = (n.textContent || '').trim();
      if (t.length > 14) continue;
      const tU = t.toUpperCase();
      for (const label of LABELS) {
        const L = label.toUpperCase();
        if (tU === L) return label;
        // icon-prefixed: "□Annex", "≡Queue" — 1-3 char prefix
        if (tU.endsWith(L) && (t.length - label.length) <= 3) return label;
      }
    }
    return null;
  }

  /* ---------- Capture-phase: explicit Annex III / Export Work Order buttons ---------- */
  // These have React onClicks that call e.stopPropagation(), so a bubble
  // listener never sees them. Capture phase runs first.
  document.addEventListener('click', function (e) {
    const btn = e.target.closest('button, a, [role="button"]');
    if (!btn) return;
    const t = (btn.textContent || '').trim();
    // Match any button/link whose visible label is fundamentally about
    // taking the Annexure III action: Export / Generate / File / Open.
    // Also matches the bare "Annex III" row link.
    const annexBtn =
      /(?:Export\s*Work\s*Order|Generate|File|Open)\s*\(?Annexure\s*III/i.test(t) ||
      /^Annex(ure)?\s*III(\s*[—-].*)?$/i.test(t);
    if (annexBtn) {
      e.preventDefault();
      e.stopPropagation();
      go(NAV.Annex);
    }
  }, true);  // ← capture phase

  /* ---------- Click handler — bubble phase ---------- */
  document.addEventListener('click', function (e) {
    // 1. Sidebar
    const side = findSidebarTarget(e.target);
    if (side && PAGE !== side) {
      e.preventDefault();
      go(NAV[side]);
      return;
    }

    // 2. Login submit / Sign-in
    if (isLogin) {
      const btn = e.target.closest('button, [role="button"], input[type="submit"], a');
      if (btn && /Sign\s*in|साइन\s*इन/i.test(btn.textContent || btn.value || '')) {
        e.preventDefault();
        captureDivision();
        go(NAV.Queue);
        return;
      }
    }

    // 3. Anchors with href="#" and recognisable text
    const a = e.target.closest('a[href="#"], a:not([href])');
    if (a) {
      const t = (a.textContent || '').trim();
      if (/View\s*cluster/i.test(t))                  { e.preventDefault(); return go(NAV.Cluster); }
      if (/Open\s*full\s*view/i.test(t))             { e.preventDefault(); return go(NAV.Defect); }
      if (/Open\s*Cluster\s*View/i.test(t))          { e.preventDefault(); return go(NAV.Cluster); }
      if (/Open\s*in\s*Priority\s*Queue/i.test(t))   { e.preventDefault(); return go(NAV.Queue); }
      if (/Open\s*Defect\s*Detail/i.test(t))         { e.preventDefault(); return go(NAV.Defect); }
      if (/Growth\s*Model|Model\s*scorecard/i.test(t)) { e.preventDefault(); return go(NAV.Model); }
    }

    // We do NOT intercept row clicks, filter toggles, modal closes, print
    // buttons, tweaks, or anything else — let the page's React handle them.
  });

  /* ---------- Login form submit ---------- */
  document.addEventListener('submit', function (e) {
    if (isLogin) {
      e.preventDefault();
      captureDivision();
      go(NAV.Queue);
    }
  });

  /* ---------- Enter key on login fields ---------- */
  if (isLogin) {
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      if (!e.target.matches('input, select')) return;
      e.preventDefault();
      captureDivision();
      go(NAV.Queue);
    });
  }

  /* ---------- After load: highlight current sidebar item, persist division ---------- */
  window.addEventListener('load', function () {
    setTimeout(decoratePage, 220);  // wait for React/Babel to mount
  });

  function decoratePage() {
    if (PAGE && PAGE !== 'Login') {
      highlightSidebar(PAGE);
    }
    restoreDivision();
  }

  function highlightSidebar(currentLabel) {
    // Find every label-bearing div inside a narrow sidebar ancestor.
    const items = {};
    const all = document.querySelectorAll('div');
    for (const d of all) {
      const t = (d.textContent || '').trim();
      if (!LABELS.includes(t)) continue;
      // verify narrow ancestor
      let p = d, narrow = false;
      for (let i = 0; p && i < 8; i++, p = p.parentElement) {
        const r = p.getBoundingClientRect();
        if (r.width > 0 && r.width < 80 && r.height > 250) { narrow = true; break; }
      }
      if (!narrow) continue;
      // Walk up to the item div (the one with cursor:pointer)
      let item = d;
      while (item && getComputedStyle(item).cursor !== 'pointer') item = item.parentElement;
      if (item && !items[t]) items[t] = item;
    }
    // Reset all then activate current.
    Object.entries(items).forEach(([label, el]) => {
      el.style.background = 'transparent';
      el.style.borderLeft = '2px solid transparent';
      el.querySelectorAll('span').forEach(s => {
        const txt = (s.textContent || '').trim();
        s.style.color = (txt === label) ? '#4A6480' : '#6B8BB0';
      });
    });
    if (items[currentLabel]) {
      const el = items[currentLabel];
      el.style.background = '#1C3F6E';
      el.style.borderLeft = '2px solid #FF7A1A';
      el.querySelectorAll('span').forEach(s => { s.style.color = '#FFFFFF'; });
    }
  }

  /* ---------- Division persistence (Mumbai — CR / Pune — CR / etc.) ---------- */
  function captureDivision() {
    document.querySelectorAll('select').forEach(sel => {
      const opt = sel.options[sel.selectedIndex];
      if (!opt) return;
      const v = (opt.textContent || opt.value || '').trim();
      if (/—\s*(CR|WR|NR|ER|SR|SCR|SER|ECR|ECoR|NCR|NWR|SECR|SWR|WCR|NER|NFR)\b/i.test(v)) {
        localStorage.setItem('echo.division', v);
      }
    });
  }

  function restoreDivision() {
    const saved = localStorage.getItem('echo.division');
    if (!saved) return;
    document.querySelectorAll('select').forEach(sel => {
      const opts = Array.from(sel.options || []);
      const m = opts.find(o => (o.textContent || '').trim() === saved);
      if (m) sel.selectedIndex = m.index;
      sel.addEventListener('change', captureDivision, { once: false });
    });
  }

  function go(href) { location.href = href; }
})();
