/* KILASI_V2_SIDEBAR_STATE_1: remembers sidebar scroll + selected submenu per browser tab.
   Works with the existing Novman dropdown handler; no AJAX page transition required. */
(function () {
  'use strict';
  var tag = document.currentScript;
  var userId = tag && tag.getAttribute('data-user-id') || 'anonymous';
  var key = 'kilasi:v2:sidebar:' + userId;
  var sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  var nav = sidebar.querySelector('.nav-list');
  if (!nav) return;

  function getSaved() {
    try {
      var value = JSON.parse(sessionStorage.getItem(key) || '{}');
      return value && typeof value === 'object' ? value : {};
    } catch (_) { return {}; }
  }
  function save(changes) {
    try {
      var state = Object.assign({}, getSaved(), changes);
      sessionStorage.setItem(key, JSON.stringify(state));
    } catch (_) { /* Restricted storage: sidebar still works normally. */ }
  }
  function groupId(group) { return group && group.getAttribute('data-nav-group') || ''; }
  function pathFor(url) {
    try {
      var u = new URL(url, window.location.href);
      if (u.origin !== window.location.origin) return null;
      var path = u.pathname.replace(/\/+$/, '');
      return path || '/';
    } catch (_) { return null; }
  }
  function activeLink() {
    var current = pathFor(window.location.href);
    var exact = null, longestPrefix = null, prefixLength = -1;
    var links = nav.querySelectorAll('a.nav-subitem[href], a.nav-item[href]');
    links.forEach(function (a) {
      var target = pathFor(a.href);
      if (!target) return;
      if (target === current) { exact = a; return; }
      // Match detail pages under each section, but not unrelated siblings.
      if (target !== '/' && current.indexOf(target + '/') === 0 && target.length > prefixLength) {
        longestPrefix = a;
        prefixLength = target.length;
      }
    });
    return exact || longestPrefix;
  }
  function markSelected() {
    nav.querySelectorAll('.nav-subitem.kilasi-selected, .nav-item.kilasi-selected').forEach(function (a) {
      a.classList.remove('kilasi-selected');
      a.removeAttribute('aria-current');
    });
    var link = activeLink();
    if (link) {
      link.classList.add('kilasi-selected');
      link.setAttribute('aria-current', 'page');
    }
    return link;
  }
  function setOpen(group, open) {
    group.classList.toggle('open', !!open);
    var button = group.querySelector('.nav-group-toggle');
    if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function restoreGroup(selected, state) {
    var group = selected && selected.closest('.nav-group');
    // Prefer the page's own submenu; otherwise preserve the previously opened group.
    var keep = groupId(group) || state.group || '';
    if (!keep) return;
    var found = false;
    nav.querySelectorAll('.nav-group').forEach(function (item) {
      var open = groupId(item) === keep;
      if (open) found = true;
      setOpen(item, open);
    });
    if (!found && group) setOpen(group, true);
  }
  function restoreScroll(state, selected) {
    var remembered = Number(state.scrollTop);
    if (Number.isFinite(remembered) && remembered >= 0) {
      sidebar.scrollTop = remembered;
    }
    // Keep the selected link in view if layout/permissions changed between pages.
    if (!selected) return;
    var item = selected.getBoundingClientRect();
    var view = sidebar.getBoundingClientRect();
    var margin = 16;
    if (item.top < view.top + margin) {
      sidebar.scrollTop -= (view.top + margin - item.top);
    } else if (item.bottom > view.bottom - margin) {
      sidebar.scrollTop += (item.bottom - view.bottom + margin);
    }
  }
  function initialize() {
    var state = getSaved();
    var selected = markSelected();
    restoreGroup(selected, state);
    requestAnimationFrame(function () { restoreScroll(state, selected); });
  }

  // Already loaded at the end of base.html, after the shared app.js.
  initialize();

  sidebar.addEventListener('scroll', function () {
    save({scrollTop: sidebar.scrollTop});
  }, {passive: true});
  nav.addEventListener('click', function (event) {
    var target = event.target.closest('a.nav-subitem[href], a.nav-item[href], .nav-group-toggle');
    if (!target || !nav.contains(target)) return;
    var group = target.closest('.nav-group');
    if (target.matches('a[href]')) {
      save({scrollTop: sidebar.scrollTop, group: groupId(group) || getSaved().group || ''});
      if (target.classList.contains('nav-subitem')) {
        nav.querySelectorAll('.nav-subitem.kilasi-selected').forEach(function (a) {
          a.classList.remove('kilasi-selected');
          a.removeAttribute('aria-current');
        });
        target.classList.add('kilasi-selected');
      }
    } else if (group) {
      // app.js toggle handler has already run by the time this bubbled listener runs.
      save({scrollTop: sidebar.scrollTop, group: group.classList.contains('open') ? groupId(group) : ''});
    }
  });
  window.addEventListener('pagehide', function () {
    save({scrollTop: sidebar.scrollTop});
  });
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) initialize();
  });
})();
