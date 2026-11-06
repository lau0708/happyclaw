import { Moon, Sun } from 'lucide-react';

import { cn } from '@/lib/utils';
import { useTheme } from '@/hooks/useTheme';

export interface ThemeToggleProps {
  className?: string;
  /** `sm` 用于桌面页头，`md` 用于手机顶栏（44px 点击区）。 */
  size?: 'sm' | 'md';
}

/**
 * 浅色/深色一键切换。
 * 深色时显示太阳（点击变亮），浅色时显示月亮（点击变暗）；
 * 两个图标叠放做旋转淡入淡出，切换时不会「跳」一下。
 */
export function ThemeToggle({ className, size = 'sm' }: ThemeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';
  const label = isDark ? '切换到浅色模式' : '切换到深色模式';

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      aria-label={label}
      title={label}
      className={cn(
        'relative inline-flex shrink-0 items-center justify-center rounded-full border',
        'border-border text-muted-foreground transition-colors',
        'hover:border-[var(--mes-accent)] hover:text-[var(--mes-accent)]',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        size === 'md' ? 'size-11' : 'size-8',
        className,
      )}
    >
      <Sun
        aria-hidden="true"
        className={cn(
          'absolute transition-all duration-300',
          size === 'md' ? 'size-5' : 'size-4',
          isDark
            ? 'rotate-0 scale-100 opacity-100'
            : '-rotate-90 scale-50 opacity-0',
        )}
      />
      <Moon
        aria-hidden="true"
        className={cn(
          'absolute transition-all duration-300',
          size === 'md' ? 'size-5' : 'size-4',
          isDark
            ? 'rotate-90 scale-50 opacity-0'
            : 'rotate-0 scale-100 opacity-100',
        )}
      />
      <span className="sr-only">{label}</span>
    </button>
  );
}
