/* Run in <head> so a saved/system theme is applied before the first paint. */
(() => {
  'use strict';
  const key = 'fishyeah-chat-theme';
  const valid = value => ['system', 'light', 'dark'].includes(value);
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const root = document.documentElement;
  let choice = 'system';
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) choice = saved;
    else {
      // Best-effort compatibility with older/custom clients; a valid new choice always wins.
      for (const legacyKey of ['fishyeah-theme', 'theme', 'darkMode']) {
        const value = localStorage.getItem(legacyKey);
        const migrated = legacyKey === 'darkMode' ? ({true:'dark', false:'light'})[value] : value === 'auto' ? 'system' : value;
        if (valid(migrated)) { choice = migrated; localStorage.setItem(key, choice); break; }
      }
    }
  } catch (_) { /* Storage may be unavailable in private/embedded browsers. */ }
  const names = {system:'跟随系统', light:'浅色模式', dark:'深色模式'};
  function apply() {
    const effective = choice === 'system' ? (system.matches ? 'dark' : 'light') : choice;
    root.dataset.theme = effective;
    root.dataset.themePreference = choice;
    root.style.colorScheme = effective;
    const chrome = document.querySelector('meta[name="theme-color"]');
    if (chrome) chrome.content = effective === 'dark' ? '#141416' : '#f5f5f7';
    const label = document.getElementById('appearanceChoice');
    if (label) label.textContent = names[choice];
    document.querySelectorAll('input[name="chat-theme"]').forEach(input => { input.checked = input.value === choice; });
  }
  apply();
  // Manual choices are independent of system appearance changes.
  if (system.addEventListener) system.addEventListener('change', apply);
  else system.addListener(apply); // Older iPhone Safari.
  window.addEventListener('storage', event => {
    try { if (event.storageArea !== localStorage) return; } catch (_) { return; }
    if (event.key === key || event.key === null) {
      choice = valid(event.newValue) ? event.newValue : 'system';
      apply();
    }
  });
  window.addEventListener('pageshow', apply);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) apply(); });
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('themeOptions').addEventListener('change', event => {
      const input = event.target;
      if (input.name !== 'chat-theme' || !valid(input.value)) return;
      choice = input.value;
      try { localStorage.setItem(key, choice); } catch (_) { /* Keep the choice for this page even without storage. */ }
      apply();
    });
    apply();
  });
})();
