import { FormEvent, KeyboardEvent, RefObject, useEffect, useRef, useState } from 'react';
import { signInTelegram } from '../api';
import { useSuggestions } from '../hooks/data';
import { localAppHref } from '../navigation';
import { BookmarkIcon, ChartIcon, ChevronDownIcon, FilmIcon, HomeIcon, LogOutIcon, MusicIcon, PlayIcon, SearchIcon, ShieldIcon, UserIcon, XIcon } from '../icons';
import type { MeResponse, Suggestion, TelegramAuthUser, User, ViewValue } from '../types';

declare global {
  interface Window {
    onTeleDirectTelegramAuth?: (user: TelegramAuthUser) => void;
  }
}

export function BottomNav({
  user,
  activeView,
  onSearch,
  onAccount,
}: {
  user: User | null;
  activeView: ViewValue | '';
  onSearch: () => void;
  onAccount: () => void;
}) {
  return (
    <nav className="bottom-nav" aria-label="Primary">
      <a className={activeView === '' ? 'active' : ''} href="/app">
        <HomeIcon />
        <span>Home</span>
      </a>
      <button type="button" onClick={onSearch}>
        <SearchIcon />
        <span>Search</span>
      </button>
      <a href="/watchlist">
        <BookmarkIcon />
        <span>Watchlist</span>
      </a>
      <a className={activeView === 'music' ? 'active' : ''} href="/app?view=music">
        <MusicIcon />
        <span>Music</span>
      </a>
      {user ? (
        <a href="/watchlist">
          <UserIcon />
          <span>Account</span>
        </a>
      ) : (
        <button type="button" onClick={onAccount}>
          <UserIcon />
          <span>Account</span>
        </button>
      )}
    </nav>
  );
}

export function Header({
  me,
  user,
  query,
  setQuery,
  searchRef,
  activeView,
  onSearchSubmit,
  onSignIn,
  onSignOut,
}: {
  me: MeResponse | null;
  user: User | null;
  query: string;
  setQuery: (next: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  activeView: ViewValue | '';
  onSearchSubmit: () => void;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement | null>(null);
  const suggestions = useSuggestions(query);

  useEffect(() => {
    if (!accountOpen) return;
    const closeOnPointer = (event: PointerEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) {
        setAccountOpen(false);
      }
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setAccountOpen(false);
    };
    document.addEventListener('pointerdown', closeOnPointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnPointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [accountOpen]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setOpen(false);
    onSearchSubmit();
  };

  const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') setOpen(false);
  };

  return (
    <header className="app-header">
      <a className="brand" href="/app" aria-label="TeleDirect">
        <span className="brand-mark">
          <PlayIcon />
        </span>
        <span>TeleDirect</span>
      </a>

      <nav className="desktop-shortcuts" aria-label="Browse">
        <a className={activeView === '' ? 'active' : ''} href="/app">Home</a>
        <a className={activeView === 'movies' ? 'active' : ''} href="/app?view=movies">Movies</a>
        <a className={activeView === 'series' ? 'active' : ''} href="/app?view=series">Series</a>
        <a className={activeView === 'music' ? 'active' : ''} href="/app?view=music">Music</a>
      </nav>

      <form className="top-search" role="search" onSubmit={handleSubmit}>
        <SearchIcon className="search-leading" />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => {
            setQuery(event.currentTarget.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKey}
          placeholder="Search library"
          autoComplete="off"
        />
        {query && (
          <button type="button" className="icon-button clear-search" onClick={() => setQuery('')} aria-label="Clear search">
            <XIcon />
          </button>
        )}
        {open && suggestions.length > 0 && (
          <SearchMenu suggestions={suggestions} onPick={() => setOpen(false)} />
        )}
      </form>

      <div className="header-actions">
        {user ? (
          <>
            <a className="icon-button" href="/watchlist" aria-label="Watchlist">
              <BookmarkIcon />
            </a>
            <div className="account-menu-wrap" ref={accountRef}>
              <button
                className="profile-chip"
                type="button"
                onClick={() => setAccountOpen((current) => !current)}
                aria-haspopup="menu"
                aria-expanded={accountOpen}
              >
                <span className="profile-avatar">
                  {user.photo ? (
                    <img src={user.photo} alt="" />
                  ) : (
                    <span>{(user.name || 'U')[0].toUpperCase()}</span>
                  )}
                </span>
                <strong>{user.name || user.username || 'User'}</strong>
                <ChevronDownIcon className="profile-chevron" />
              </button>
              {accountOpen && (
                <div className="account-menu" role="menu">
                  <a href="/watchlist" role="menuitem" onClick={() => setAccountOpen(false)}>
                    <BookmarkIcon />
                    <span>Watchlist</span>
                  </a>
                  <a href="/stats" role="menuitem" onClick={() => setAccountOpen(false)}>
                    <ChartIcon />
                    <span>Stats</span>
                  </a>
                  {user.is_admin && (
                    <a href="/admin" role="menuitem" onClick={() => setAccountOpen(false)}>
                      <ShieldIcon />
                      <span>Admin panel</span>
                    </a>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setAccountOpen(false);
                      onSignOut();
                    }}
                  >
                    <LogOutIcon />
                    <span>Sign out</span>
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          <button className="signin-button" type="button" onClick={onSignIn} disabled={me === null}>
            <UserIcon />
            <span>Sign in</span>
          </button>
        )}
      </div>
    </header>
  );
}

export function SearchMenu({ suggestions, onPick }: { suggestions: Suggestion[]; onPick: () => void }) {
  return (
    <div className="search-menu">
      {suggestions.map((item) => (
        <a key={item.url} href={localAppHref(item.url) || item.url} className="suggestion" onClick={onPick}>
          <span className="suggestion-art">
            {item.poster_path ? (
              <img src={`https://image.tmdb.org/t/p/w92${item.poster_path}`} alt="" loading="lazy" />
            ) : (
              <img src={`/thumb/${item.secure_hash}${item.message_id}.jpg`} alt="" loading="lazy" />
            )}
          </span>
          <span className="suggestion-copy">
            <strong>{item.title}</strong>
            <span>{[item.year, item.kind].filter(Boolean).join(' - ')}</span>
          </span>
        </a>
      ))}
    </div>
  );
}


export function SignInModal({
  open,
  botUsername,
  onClose,
}: {
  open: boolean;
  botUsername: string;
  onClose: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || !botUsername || !rootRef.current) return;
    rootRef.current.innerHTML = '';
    setError('');

    window.onTeleDirectTelegramAuth = async (telegramUser: TelegramAuthUser) => {
      try {
        const data = await signInTelegram(telegramUser);
        if (data.token) sessionStorage.setItem('td:auth', data.token);
        window.location.reload();
      } catch (_) {
        setError('Sign in failed');
      }
    };

    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.setAttribute('data-telegram-login', botUsername.replace(/^@/, ''));
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '8');
    script.setAttribute('data-onauth', 'onTeleDirectTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    rootRef.current.appendChild(script);

    return () => {
      delete window.onTeleDirectTelegramAuth;
      if (rootRef.current) rootRef.current.innerHTML = '';
    };
  }, [botUsername, open]);

  if (!open) return null;

  return (
    <div className="modal-layer" role="dialog" aria-modal="true" aria-label="Sign in">
      <button className="modal-scrim" type="button" onClick={onClose} aria-label="Close" />
      <div className="modal-panel">
        <button className="icon-button modal-close" type="button" onClick={onClose} aria-label="Close">
          <XIcon />
        </button>
        <h2>Sign in</h2>
        <div className="telegram-slot" ref={rootRef} />
        {!botUsername && <p className="form-error">Telegram login unavailable</p>}
        {error && <p className="form-error">{error}</p>}
      </div>
    </div>
  );
}

