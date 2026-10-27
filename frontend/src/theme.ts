// Theme management: dark / light / system, persisted preference (localStorage),
// applied on <html data-theme=...> so both Electron and web behave identically.

export type ThemeMode = 'dark' | 'light' | 'system';
export const THEME_KEY = 'trading-opus-theme';

export function resolvedTheme(mode: ThemeMode): 'dark' | 'light' {
  if (mode !== 'system') return mode;
  const mq = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: light)')
    : { matches: false };
  return mq.matches ? 'light' : 'dark';
}

export function applyTheme(mode: ThemeMode): 'dark' | 'light' {
  const r = resolvedTheme(mode);
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', r);
  }
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(THEME_KEY, mode);
  }
  return r;
}

export function loadTheme(): ThemeMode {
  const saved = typeof localStorage !== 'undefined'
    ? (localStorage.getItem(THEME_KEY) as ThemeMode | null)
    : null;
  return saved ?? 'dark';
}

// keep 'system' live when the OS preference changes
export function watchSystem(mode: ThemeMode, onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {};
  const mq = window.matchMedia('(prefers-color-scheme: light)');
  const handler = () => { if (mode === 'system') onChange(); };
  mq.addEventListener('change', handler);
  return () => mq.removeEventListener('change', handler);
}
