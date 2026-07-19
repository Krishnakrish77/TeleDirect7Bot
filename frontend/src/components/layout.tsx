import { FormEvent, KeyboardEvent, RefObject, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { signInTelegram } from '../api';
import { useSuggestions } from '../hooks/data';
import { localAppHref } from '../navigation';
import { BookmarkIcon, BroadcastIcon, ChartIcon, ChevronDownIcon, ChevronUpIcon, FilmIcon, HeartIcon, HomeIcon, ListIcon, LogOutIcon, MusicIcon, PlayIcon, SearchIcon, ShieldIcon, TvIcon, UserIcon, XIcon } from '../icons';
import type { MeResponse, Suggestion, TelegramAuthUser, User, ViewValue } from '../types';
import { tmdbImageUrl } from '../utils/tmdb';

declare global {
  interface Window {
    onTeleDirectTelegramAuth?: (user: TelegramAuthUser) => void;
  }
}

export function PrimaryNav({
  user,
  activeView,
  activeSection,
}: {
  user: User | null;
  activeView: ViewValue | '';
  activeSection: 'home' | 'movies' | 'series' | 'music' | 'live-tv' | 'watchlist' | 'liked-songs' | 'playlists' | 'stats' | '';
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreButtonRef = useRef<HTMLButtonElement | null>(null);
  const moreMenuRef = useRef<HTMLDivElement | null>(null);
  const moreActive = ['series', 'live-tv', 'watchlist', 'liked-songs', 'playlists', 'stats'].includes(activeSection);

  useEffect(() => {
    if (!moreOpen) return undefined;
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!moreMenuRef.current?.contains(target) && !moreButtonRef.current?.contains(target)) setMoreOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMoreOpen(false);
        moreButtonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', closeOnOutsidePress);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePress);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [moreOpen]);
  return (
    <nav className="primary-nav" aria-label="Primary">
      <a className={activeSection === 'home' ? 'active' : ''} href="/app">
        <HomeIcon />
        <span>Home</span>
      </a>
      <a className={activeView === 'movies' && activeSection === 'movies' ? 'active' : ''} href="/app?view=movies">
        <FilmIcon />
        <span>Movies</span>
      </a>
      <a className={activeView === 'series' && activeSection === 'series' ? 'mobile-secondary active' : 'mobile-secondary'} href="/app?view=series">
        <TvIcon />
        <span>Series</span>
      </a>
      <a className={activeView === 'music' && activeSection === 'music' ? 'active' : ''} href="/app?view=music">
        <MusicIcon />
        <span>Music</span>
      </a>
      {user && (
        <a className={activeSection === 'liked-songs' ? 'mobile-secondary active' : 'mobile-secondary'} href="/app/liked-songs" aria-label="Liked Songs">
          <HeartIcon />
          <span>Liked Songs</span>
        </a>
      )}
      <a className={activeSection === 'live-tv' ? 'mobile-secondary active' : 'mobile-secondary'} href="/app/live-tv">
        <BroadcastIcon />
        <span>Live TV</span>
      </a>
      {user && (
        <a className={activeSection === 'watchlist' ? 'mobile-secondary active' : 'mobile-secondary'} href="/app/watchlist">
          <BookmarkIcon />
          <span>Watchlist</span>
        </a>
      )}
      <button
        type="button"
        ref={moreButtonRef}
        className={moreActive ? 'mobile-nav-more active' : 'mobile-nav-more'}
        aria-expanded={moreOpen}
        aria-controls="mobile-nav-more-sheet"
        aria-haspopup="menu"
        onClick={() => setMoreOpen((open) => !open)}
      >
        <ListIcon />
        <span>More</span>
      </button>
      {moreOpen && (
        <div ref={moreMenuRef} className="mobile-nav-sheet" id="mobile-nav-more-sheet" role="menu" aria-label="More navigation">
          <a href="/app?view=series" role="menuitem" onClick={() => setMoreOpen(false)}><TvIcon /><span>Series</span></a>
          <a href="/app/live-tv" role="menuitem" onClick={() => setMoreOpen(false)}><BroadcastIcon /><span>Live TV</span></a>
          {user && <a href="/app/watchlist" role="menuitem" onClick={() => setMoreOpen(false)}><BookmarkIcon /><span>Watchlist</span></a>}
          {user && <a href="/app/liked-songs" role="menuitem" onClick={() => setMoreOpen(false)}><HeartIcon /><span>Liked Songs</span></a>}
          {user && <a href="/app/playlists" role="menuitem" onClick={() => setMoreOpen(false)}><ListIcon /><span>Playlists</span></a>}
          {user && <a href="/app/stats" role="menuitem" onClick={() => setMoreOpen(false)}><ChartIcon /><span>Stats</span></a>}
        </div>
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
  accountOpen,
  setAccountOpen,
  classicUiHref,
  onSearchSubmit,
  onSearchClear,
  onSuggestionNavigate,
  onSignIn,
  onSignOut,
}: {
  me: MeResponse | null;
  user: User | null;
  query: string;
  setQuery: (next: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  accountOpen: boolean;
  setAccountOpen: Dispatch<SetStateAction<boolean>>;
  classicUiHref: string;
  onSearchSubmit: () => void;
  onSearchClear: () => void;
  onSuggestionNavigate: (href: string) => void;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [avatarFailed, setAvatarFailed] = useState(false);
  const accountRef = useRef<HTMLDivElement | null>(null);
  const searchWrapRef = useRef<HTMLFormElement | null>(null);
  const suggestions = useSuggestions(query.trim());
  const suggestionsOpen = open && suggestions.length > 0;
  const activeSuggestionId = suggestionsOpen && activeIndex >= 0 ? `top-search-suggestion-${activeIndex}` : undefined;

  // Telegram Login Widget photo URLs can expire before the session JWT does.
  // Reset the fallback if a different account/photo is received.
  useEffect(() => setAvatarFailed(false), [user?.photo, user?.sub]);

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
  }, [accountOpen, setAccountOpen]);

  useEffect(() => {
    setActiveIndex((current) => {
      if (!suggestions.length) return -1;
      return current >= suggestions.length ? suggestions.length - 1 : current;
    });
  }, [suggestions.length]);

  useEffect(() => {
    if (!open) return;
    const closeOnPointer = (event: PointerEvent) => {
      if (!searchWrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    };
    document.addEventListener('pointerdown', closeOnPointer);
    return () => document.removeEventListener('pointerdown', closeOnPointer);
  }, [open]);

  const closeSuggestions = () => {
    setOpen(false);
    setActiveIndex(-1);
  };

  const suggestionHref = (item: Suggestion) => localAppHref(item.url) || item.url;

  const pickSuggestion = (item: Suggestion) => {
    closeSuggestions();
    searchRef.current?.blur();
    const href = suggestionHref(item);
    if (href.startsWith('/app')) {
      onSuggestionNavigate(href);
    } else {
      window.location.href = href;
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    closeSuggestions();
    onSearchSubmit();
  };

  const handleClear = () => {
    closeSuggestions();
    onSearchClear();
  };

  const handleKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      closeSuggestions();
      return;
    }
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => {
        if (event.key === 'ArrowDown') return current >= suggestions.length - 1 ? 0 : current + 1;
        return current <= 0 ? suggestions.length - 1 : current - 1;
      });
      return;
    }
    if (event.key === 'Enter' && suggestionsOpen && activeIndex >= 0) {
      event.preventDefault();
      const item = suggestions[activeIndex];
      if (item) pickSuggestion(item);
    }
  };

  return (
    <header className="app-header">
      <a className="brand" href="/app" aria-label="TeleDirect">
        <span className="brand-mark">
          <PlayIcon />
        </span>
        <span>TeleDirect</span>
      </a>

      <form className="top-search" role="search" onSubmit={handleSubmit} ref={searchWrapRef}>
        <button type="submit" className="icon-button search-leading search-submit" aria-label="Search">
          <SearchIcon />
        </button>
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
          aria-autocomplete="list"
          aria-expanded={suggestionsOpen}
          aria-controls="top-search-suggestions"
          aria-activedescendant={activeSuggestionId}
        />
        {query && (
          <button type="button" className="icon-button clear-search" onClick={handleClear} aria-label="Clear search">
            <XIcon />
          </button>
        )}
        {suggestionsOpen && (
          <SearchMenu
            suggestions={suggestions}
            activeIndex={activeIndex}
            getHref={suggestionHref}
            onActiveIndexChange={setActiveIndex}
            onPick={closeSuggestions}
          />
        )}
      </form>

      <div className="header-actions">
        <a className="ui-switch-button" href={classicUiHref}>
          <span>Classic UI</span>
        </a>
        {user ? (
          <div className="account-menu-wrap" ref={accountRef}>
            <button
              className="profile-chip"
              type="button"
              onClick={() => setAccountOpen((current) => !current)}
              aria-haspopup="menu"
              aria-expanded={accountOpen}
            >
              <span className="profile-avatar">
                {user.photo && !avatarFailed ? (
                  <img src={user.photo} alt="" onError={() => setAvatarFailed(true)} />
                ) : (
                  <span>{(user.name || 'U')[0].toUpperCase()}</span>
                )}
              </span>
              <strong>{user.name || user.username || 'User'}</strong>
              <ChevronDownIcon className="profile-chevron" />
            </button>
            {accountOpen && (
              <div className="account-menu" role="menu">
                <div className="account-menu-identity" aria-hidden="true">
                  <strong>{user.name || user.username || 'Signed in'}</strong>
                  {user.username && <span>@{user.username}</span>}
                </div>
                {user.is_admin && (
                  <a href="/app/admin" role="menuitem" onClick={() => setAccountOpen(false)}>
                    <ShieldIcon />
                    <span>Admin panel</span>
                  </a>
                )}
                <span className="account-menu-divider" aria-hidden="true" />
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

export function SearchMenu({
  suggestions,
  activeIndex,
  getHref,
  onActiveIndexChange,
  onPick,
}: {
  suggestions: Suggestion[];
  activeIndex: number;
  getHref: (item: Suggestion) => string;
  onActiveIndexChange: (index: number) => void;
  onPick: () => void;
}) {
  return (
    <div className="search-menu" id="top-search-suggestions" role="listbox">
      {suggestions.map((item, index) => {
        const isAudio = item.media_kind === 'audio' || item.kind === 'audio' || item.kind === 'album';
        const fallbackArt = `/thumb/${item.secure_hash}${item.message_id}.jpg${isAudio ? '?v=audio3' : ''}`;
        return (
          <a
            key={item.url}
            id={`top-search-suggestion-${index}`}
            href={getHref(item)}
            role="option"
            aria-selected={index === activeIndex}
            className={index === activeIndex ? 'suggestion active' : 'suggestion'}
            onMouseEnter={() => onActiveIndexChange(index)}
            onClick={onPick}
          >
            <span className="suggestion-art">
              {item.poster_path ? (
                <img src={tmdbImageUrl(item.poster_path, 'w92')} alt="" loading="lazy" decoding="async" />
              ) : (
                <img src={fallbackArt} alt="" loading="lazy" decoding="async" />
              )}
            </span>
            <span className="suggestion-copy">
              <strong>{item.title}</strong>
              <span>{[item.year, item.kind].filter(Boolean).join(' - ')}</span>
            </span>
          </a>
        );
      })}
    </div>
  );
}


export function ScrollToTop() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 400);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  if (!visible) return null;
  return (
    <button
      type="button"
      className="scroll-top-btn"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      aria-label="Scroll to top"
    >
      <ChevronUpIcon />
    </button>
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
