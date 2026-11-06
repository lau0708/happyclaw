import { useCallback, useEffect, useSyncExternalStore } from 'react';

export type Theme = 'light' | 'dark' | 'system';
export type ColorScheme = 'default' | 'orange' | 'neutral';
export type FontStyle = 'default' | 'anthropic';

const THEME_KEY = 'happyclaw-theme';
const SCHEME_KEY = 'happyclaw-color-scheme';
const FONT_KEY = 'happyclaw-font-style';
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((cb) => cb());
}

function getSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

// 默认深色：本项目采用工业深色大屏视觉（与设备业务页一致），
// 未设置过主题的用户直接进入深色，避免浅色页面与设备大屏割裂。
// 存储语义——无 key = dark；三个取值都显式落 key。
export const DEFAULT_THEME: Theme = 'dark';

function readTheme(): Theme {
  if (typeof window === 'undefined') return DEFAULT_THEME;
  const stored = window.localStorage.getItem(THEME_KEY);
  if (stored === 'light' || stored === 'dark' || stored === 'system')
    return stored;
  return DEFAULT_THEME;
}

function readColorScheme(): ColorScheme {
  if (typeof window === 'undefined') return 'orange';
  const stored = window.localStorage.getItem(SCHEME_KEY);
  if (stored === 'default' || stored === 'orange' || stored === 'neutral')
    return stored;
  return 'orange';
}

function readFontStyle(): FontStyle {
  if (typeof window === 'undefined') return 'default';
  const stored = window.localStorage.getItem(FONT_KEY);
  if (stored === 'default' || stored === 'anthropic') return stored;
  return 'default';
}

function resolveTheme(theme: Theme): 'light' | 'dark' {
  return theme === 'system' ? getSystemTheme() : theme;
}

function syncMetaThemeColor() {
  if (typeof document === 'undefined') return;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  // The browser chrome follows whatever --background the active scheme and
  // mode resolve to, so every palette stays in sync without a lookup table.
  const background = getComputedStyle(document.documentElement)
    .getPropertyValue('--background')
    .trim();
  if (background) meta.setAttribute('content', background);
}

export function applyTheme(theme: Theme) {
  if (typeof document === 'undefined') return;
  document.documentElement.classList.toggle(
    'dark',
    resolveTheme(theme) === 'dark',
  );
  syncMetaThemeColor();
}

function applyColorScheme(scheme: ColorScheme) {
  if (typeof document === 'undefined') return;
  document.documentElement.classList.toggle(
    'theme-orange',
    scheme === 'orange',
  );
  document.documentElement.classList.toggle(
    'theme-neutral',
    scheme === 'neutral',
  );
  syncMetaThemeColor();
}

function applyFontStyle(style: FontStyle) {
  if (typeof document === 'undefined') return;
  document.documentElement.classList.toggle(
    'font-anthropic',
    style === 'anthropic',
  );
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useTheme() {
  const theme = useSyncExternalStore(
    subscribe,
    readTheme,
    () => DEFAULT_THEME,
  );
  const colorScheme = useSyncExternalStore(
    subscribe,
    readColorScheme,
    () => 'orange' as ColorScheme,
  );
  const fontStyle = useSyncExternalStore(
    subscribe,
    readFontStyle,
    () => 'default' as FontStyle,
  );

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    applyColorScheme(colorScheme);
  }, [colorScheme]);
  useEffect(() => {
    applyFontStyle(fontStyle);
  }, [fontStyle]);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      if (readTheme() === 'system') {
        applyTheme('system');
        notify();
      }
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const setTheme = useCallback((t: Theme) => {
    // 默认值是 dark，因此三个取值都显式落盘，避免「删除 key」被读回默认深色
    window.localStorage.setItem(THEME_KEY, t);
    applyTheme(t);
    notify();
  }, []);

  const setColorScheme = useCallback((s: ColorScheme) => {
    if (s === 'orange') window.localStorage.removeItem(SCHEME_KEY);
    else window.localStorage.setItem(SCHEME_KEY, s);
    applyColorScheme(s);
    notify();
  }, []);

  const setFontStyle = useCallback((f: FontStyle) => {
    if (f === 'default') window.localStorage.removeItem(FONT_KEY);
    else window.localStorage.setItem(FONT_KEY, f);
    applyFontStyle(f);
    notify();
  }, []);

  const toggle = useCallback(() => {
    const next: Theme =
      theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light';
    setTheme(next);
  }, [theme, setTheme]);

  return {
    theme,
    resolvedTheme: resolveTheme(theme),
    colorScheme,
    fontStyle,
    toggle,
    setTheme,
    setColorScheme,
    setFontStyle,
  };
}
