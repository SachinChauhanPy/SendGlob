/**
 * emailOPS - app.js
 * Lucide icon rendering + Dark-mode toggle + Auto-dismiss alerts + Collapsible sidebar
 */

/**
 * Swap the Lucide icon inside a container in place, preserving sibling content
 * (e.g. a button's text label). Works whether the current icon is still an
 * unrendered <i data-lucide> placeholder or an already-rendered <svg>.
 */
window.setIcon = (container, name) => {
  if (!container) return;
  const current = container.querySelector('svg, i[data-lucide]');
  const placeholder = document.createElement('i');
  placeholder.setAttribute('data-lucide', name);
  if (current) {
    current.replaceWith(placeholder);
  } else {
    container.prepend(placeholder);
  }
  if (window.lucide) lucide.createIcons();
};

document.addEventListener('DOMContentLoaded', () => {
  // ── Render Lucide icons in the initial markup ──
  if (window.lucide) lucide.createIcons();

  // ── Dark Mode Toggle (supports multiple buttons) ──
  const toggles = document.querySelectorAll('.theme-toggle');
  if (toggles.length > 0) {
    const htmlEl = document.documentElement;

    const applyTheme = (theme) => {
      htmlEl.setAttribute('data-bs-theme', theme);
      const iconName = theme === 'dark' ? 'sun' : 'moon';
      toggles.forEach(btn => {
        btn.innerHTML = `<i data-lucide="${iconName}"></i>`;
      });
      if (window.lucide) lucide.createIcons();
    };

    // Initialise from localStorage or system preference
    const saved = localStorage.getItem('theme');
    if (saved) {
      applyTheme(saved);
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      applyTheme('dark');
    }

    toggles.forEach(btn => {
      btn.addEventListener('click', () => {
        const next = htmlEl.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        localStorage.setItem('theme', next);
      });
    });
  }

  // ── Auto-dismiss alerts after 5s ──
  document.querySelectorAll('.alert-dismissible').forEach(el => {
    setTimeout(() => {
      const inst = bootstrap.Alert.getOrCreateInstance(el);
      if (inst) inst.close();
    }, 5000);
  });

  // ── Collapsible desktop sidebar ──
  const sidebar = document.getElementById('appSidebar');
  const collapseBtn = document.getElementById('sidebarCollapseBtn');
  const SIDEBAR_KEY = 'sidebarCollapsed';

  if (sidebar && collapseBtn) {
    const applyCollapsed = (collapsed) => {
      sidebar.classList.toggle('sidebar-collapsed', collapsed);
      collapseBtn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
      setIcon(collapseBtn, collapsed ? 'panel-left-open' : 'panel-left-close');
    };

    // Restore persisted state before first paint (avoids layout flash).
    // We apply the class immediately here (DOMContentLoaded fires before paint).
    applyCollapsed(localStorage.getItem(SIDEBAR_KEY) === '1');

    collapseBtn.addEventListener('click', () => {
      const next = !sidebar.classList.contains('sidebar-collapsed');
      applyCollapsed(next);
      localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0');
    });
  }
});
