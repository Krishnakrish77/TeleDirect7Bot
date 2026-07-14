import { useCallback, useEffect, useState } from 'react';
import { DownloadIcon, ShareIcon, XIcon } from '../icons';

type InstallChoice = {
  outcome: 'accepted' | 'dismissed';
  platform: string;
};

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<InstallChoice>;
};

type InstallMode = 'native' | 'ios';
type InstallPromptProps = {
  enabled?: boolean;
};

const DISMISS_KEY = 'td:pwa-install-dismissed-until:v1';
const DISMISS_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function readDismissedUntil(): number {
  try {
    const value = Number(window.localStorage.getItem(DISMISS_KEY) || 0);
    return Number.isFinite(value) ? value : 0;
  } catch {
    return 0;
  }
}

function rememberDismissal(): number {
  const until = Date.now() + DISMISS_TTL_MS;
  try {
    window.localStorage.setItem(DISMISS_KEY, String(until));
  } catch {
    // Private browsing modes can reject storage writes; hiding for this render
    // still avoids nagging the user repeatedly during the same session.
  }
  return until;
}

function clearDismissal() {
  try {
    window.localStorage.removeItem(DISMISS_KEY);
  } catch {
    // Storage is best-effort for this prompt.
  }
}

function isDismissed(): boolean {
  return readDismissedUntil() > Date.now();
}

function isStandalone(): boolean {
  const navigatorWithStandalone = window.navigator as Navigator & { standalone?: boolean };
  return Boolean(
    navigatorWithStandalone.standalone ||
    (typeof window.matchMedia === 'function' && window.matchMedia('(display-mode: standalone)').matches),
  );
}

function isIosDevice(): boolean {
  const { platform, userAgent, maxTouchPoints } = window.navigator;
  return /iphone|ipad|ipod/i.test(userAgent) || (platform === 'MacIntel' && maxTouchPoints > 1);
}

export function InstallPrompt({ enabled = true }: InstallPromptProps) {
  const [installEvent, setInstallEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [mode, setMode] = useState<InstallMode | null>(null);
  const [installed, setInstalled] = useState(() => isStandalone());
  const [dismissedUntil, setDismissedUntil] = useState(() => readDismissedUntil());

  useEffect(() => {
    if (!enabled) {
      setMode(null);
      return;
    }
    if (isStandalone()) {
      setInstalled(true);
      return;
    }
    if (isDismissed()) return;
    if (installEvent) {
      setMode('native');
      return;
    }
    if (isIosDevice()) setMode('ios');
  }, [enabled, installEvent]);

  useEffect(() => {
    const onBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      if (isStandalone() || isDismissed()) return;
      setInstallEvent(event as BeforeInstallPromptEvent);
      setMode(enabled ? 'native' : null);
    };
    const onAppInstalled = () => {
      clearDismissal();
      setInstalled(true);
      setInstallEvent(null);
      setMode(null);
    };

    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt);
    window.addEventListener('appinstalled', onAppInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt);
      window.removeEventListener('appinstalled', onAppInstalled);
    };
  }, [enabled]);

  const dismiss = useCallback(() => {
    setDismissedUntil(rememberDismissal());
    setInstallEvent(null);
    setMode(null);
  }, []);

  const install = useCallback(async () => {
    if (!installEvent) return;
    try {
      await installEvent.prompt();
      const choice = await installEvent.userChoice;
      if (choice.outcome === 'accepted') {
        clearDismissal();
        setInstalled(true);
      } else {
        setDismissedUntil(rememberDismissal());
      }
    } catch {
      setDismissedUntil(rememberDismissal());
    } finally {
      setInstallEvent(null);
      setMode(null);
    }
  }, [installEvent]);

  if (!enabled || installed || !mode || dismissedUntil > Date.now()) return null;

  const isNative = mode === 'native' && installEvent;
  const title = isNative ? 'Install TeleDirect' : 'Add TeleDirect to Home Screen';
  const body = isNative
    ? 'Open it from your home screen.'
    : 'Use Share, then Add to Home Screen.';

  return (
    <section className="install-prompt" aria-label="Install TeleDirect">
      <div className="install-prompt-icon" aria-hidden="true">
        {isNative ? <DownloadIcon /> : <ShareIcon />}
      </div>
      <div className="install-prompt-copy">
        <strong>{title}</strong>
        <span>{body}</span>
      </div>
      <div className="install-prompt-actions">
        {isNative ? (
          <button type="button" className="primary-action install-prompt-primary" onClick={install}>
            <DownloadIcon />
            Install
          </button>
        ) : null}
        <button type="button" className="icon-button" aria-label="Dismiss install prompt" onClick={dismiss}>
          <XIcon />
        </button>
      </div>
    </section>
  );
}
