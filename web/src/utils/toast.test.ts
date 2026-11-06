// @vitest-environment happy-dom
import { afterEach, describe, expect, test, vi } from 'vitest';

const toastMock = vi.hoisted(() => vi.fn());
vi.mock('sonner', () => ({ toast: toastMock }));

import { notifyIfHidden, showToast } from './toast';

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    get: () => hidden,
  });
}

afterEach(() => {
  toastMock.mockReset();
  vi.unstubAllGlobals();
  setHidden(false);
});

describe('showToast', () => {
  test('goes through sonner with the same call signature', () => {
    showToast('发送失败', 'x'.repeat(130), 8000, {
      text: '查看',
      url: 'https://example.com/a',
    });
    expect(toastMock).toHaveBeenCalledTimes(1);
    const [title, options] = toastMock.mock.calls[0];
    expect(title).toBe('发送失败');
    expect(options.description).toBe('x'.repeat(120) + '…');
    expect(options.duration).toBe(8000);
    expect(options.action.label).toBe('查看');

    const open = vi.fn();
    vi.stubGlobal('open', open);
    options.action.onClick();
    expect(open).toHaveBeenCalledWith(
      'https://example.com/a',
      '_blank',
      'noopener,noreferrer',
    );
  });

  test('defaults to five seconds and no action', () => {
    showToast('上传失败');
    expect(toastMock).toHaveBeenCalledWith('上传失败', {
      description: undefined,
      duration: 5000,
      action: undefined,
    });
  });
});

describe('notifyIfHidden', () => {
  test('a Notification constructor that throws never reaches the caller', () => {
    const Throwing = vi.fn(function () {
      throw new TypeError('Illegal constructor');
    });
    vi.stubGlobal(
      'Notification',
      Object.assign(Throwing, { permission: 'granted' }),
    );
    setHidden(true);

    let stateUpdated = false;
    expect(() => {
      notifyIfHidden('PulseOps', 'done');
      stateUpdated = true;
    }).not.toThrow();
    expect(Throwing).toHaveBeenCalledTimes(1);
    expect(stateUpdated).toBe(true);
  });

  test('after a throw the permission prompt is no longer offered', async () => {
    vi.resetModules();
    const fresh = await import('./toast');
    vi.stubGlobal(
      'Notification',
      Object.assign(
        vi.fn(function () {
          throw new TypeError('Illegal constructor');
        }),
        { permission: 'granted' },
      ),
    );
    setHidden(true);
    fresh.notifyIfHidden('PulseOps');

    (Notification as unknown as { permission: string }).permission = 'default';
    fresh.showNotificationPromptToast();
    expect(toastMock).not.toHaveBeenCalled();
  });

  test('the permission prompt is offered while permission is undecided', async () => {
    vi.resetModules();
    const fresh = await import('./toast');
    vi.stubGlobal(
      'Notification',
      Object.assign(vi.fn(), { permission: 'default' }),
    );
    fresh.showNotificationPromptToast();
    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(toastMock.mock.calls[0][1].action.label).toBe('开启');
  });

  test('stays silent while the page is visible', () => {
    const Ctor = vi.fn();
    vi.stubGlobal(
      'Notification',
      Object.assign(Ctor, { permission: 'granted' }),
    );
    setHidden(false);
    notifyIfHidden('PulseOps');
    expect(Ctor).not.toHaveBeenCalled();
  });
});
