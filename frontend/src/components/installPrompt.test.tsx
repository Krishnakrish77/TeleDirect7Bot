import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { InstallPrompt } from './installPrompt';

const DISMISS_KEY = 'td:pwa-install-dismissed-until:v1';

function mockDisplayMode(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function mockNavigator(value: Partial<Navigator>) {
  for (const [key, nextValue] of Object.entries(value)) {
    Object.defineProperty(window.navigator, key, {
      configurable: true,
      value: nextValue,
    });
  }
}

function makeBeforeInstallPrompt(outcome: 'accepted' | 'dismissed' = 'accepted') {
  const event = new Event('beforeinstallprompt', { cancelable: true }) as Event & {
    prompt: ReturnType<typeof vi.fn>;
    userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
  };
  Object.defineProperties(event, {
    prompt: { value: vi.fn().mockResolvedValue(undefined) },
    userChoice: { value: Promise.resolve({ outcome, platform: 'web' }) },
  });
  return event;
}

describe('InstallPrompt', () => {
  beforeEach(() => {
    mockDisplayMode(false);
    mockNavigator({
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
      platform: 'MacIntel',
      maxTouchPoints: 0,
    });
  });

  it('uses the browser install prompt when available', async () => {
    const event = makeBeforeInstallPrompt('accepted');
    render(<InstallPrompt />);

    window.dispatchEvent(event);

    fireEvent.click(await screen.findByRole('button', { name: 'Install' }));

    await waitFor(() => expect(event.prompt).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByLabelText('Install TeleDirect')).toBeNull());
    expect(window.localStorage.getItem(DISMISS_KEY)).toBeNull();
  });

  it('remembers dismissed native prompts', async () => {
    const event = makeBeforeInstallPrompt('dismissed');
    const view = render(<InstallPrompt />);

    window.dispatchEvent(event);
    fireEvent.click(await screen.findByRole('button', { name: 'Install' }));

    await waitFor(() => expect(Number(window.localStorage.getItem(DISMISS_KEY))).toBeGreaterThan(Date.now()));

    view.unmount();
    render(<InstallPrompt />);
    window.dispatchEvent(makeBeforeInstallPrompt('accepted'));

    expect(screen.queryByRole('button', { name: 'Install' })).toBeNull();
  });

  it('shows iOS home-screen guidance when no native prompt exists', async () => {
    mockNavigator({
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)',
      platform: 'iPhone',
      maxTouchPoints: 5,
    });

    render(<InstallPrompt />);

    expect(await screen.findByText('Use Share, then Add to Home Screen.')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Dismiss install prompt'));

    expect(Number(window.localStorage.getItem(DISMISS_KEY))).toBeGreaterThan(Date.now());
  });

  it('stays hidden when already running standalone', () => {
    mockDisplayMode(true);

    render(<InstallPrompt />);

    expect(screen.queryByLabelText('Install TeleDirect')).toBeNull();
  });

  it('stays hidden when disabled for guests', () => {
    const event = makeBeforeInstallPrompt('accepted');
    render(<InstallPrompt enabled={false} />);

    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(screen.queryByLabelText('Install TeleDirect')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Install' })).toBeNull();
  });
});
