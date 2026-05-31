import { useCallback, useEffect, useRef, useState } from 'react';
import type { WatchTrack } from '../types';

export interface PlayerState {
  track: WatchTrack | null;
  queue: WatchTrack[];
  queueIndex: number;
  playing: boolean;
  currentTime: number;
  duration: number;
  error: string;
}

export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0:00';
  const whole = Math.floor(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

export function useAudioPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [player, setPlayer] = useState<PlayerState>({
    track: null,
    queue: [],
    queueIndex: -1,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: '',
  });
  const playerRef = useRef(player);

  useEffect(() => {
    playerRef.current = player;
  }, [player]);

  const startAudio = useCallback((track: WatchTrack, reset = false) => {
    const audio = audioRef.current;
    if (!audio) return;
    const nextSrc = new URL(track.streamHref, window.location.origin).href;
    if (audio.src !== nextSrc) {
      audio.src = track.streamHref;
      reset = true;
    }
    if (reset) audio.currentTime = 0;
    const promise = audio.play();
    if (promise) {
      promise.catch(() => {
        setPlayer((current) => ({
          ...current,
          playing: false,
          error: 'Tap play to start audio.',
        }));
      });
    }
  }, []);

  const playTrack = useCallback((track: WatchTrack, queue?: WatchTrack[]) => {
    const current = playerRef.current;
    const nextQueue = queue?.length ? queue : current.queue.length ? current.queue : [track];
    const found = nextQueue.findIndex((item) => item.key === track.key);
    const queueIndex = found >= 0 ? found : 0;
    const sameTrack = current.track?.key === track.key;
    setPlayer((state) => ({
      ...state,
      track,
      queue: nextQueue,
      queueIndex,
      playing: true,
      currentTime: sameTrack ? state.currentTime : 0,
      duration: sameTrack ? state.duration : track.duration || 0,
      error: '',
    }));
    startAudio(track, !sameTrack);
  }, [startAudio]);

  const playRelative = useCallback((delta: number) => {
    const current = playerRef.current;
    const nextIndex = current.queueIndex + delta;
    const nextTrack = current.queue[nextIndex];
    if (!nextTrack) {
      setPlayer((state) => ({ ...state, playing: false }));
      return;
    }
    setPlayer((state) => ({
      ...state,
      track: nextTrack,
      queueIndex: nextIndex,
      playing: true,
      currentTime: 0,
      duration: nextTrack.duration || 0,
      error: '',
    }));
    startAudio(nextTrack, true);
  }, [startAudio]);

  const playQueueIndex = useCallback((index: number) => {
    const current = playerRef.current;
    const nextTrack = current.queue[index];
    if (!nextTrack) return;
    setPlayer((state) => ({
      ...state,
      track: nextTrack,
      queueIndex: index,
      playing: true,
      currentTime: 0,
      duration: nextTrack.duration || 0,
      error: '',
    }));
    startAudio(nextTrack, true);
  }, [startAudio]);

  const addToQueue = useCallback((track: WatchTrack, playNext = false) => {
    setPlayer((state) => {
      if (!state.track) {
        window.setTimeout(() => playTrack(track, [track]), 0);
        return state;
      }
      const queue = state.queue.length ? [...state.queue] : [state.track];
      const existing = queue.findIndex((item) => item.key === track.key);
      if (existing >= 0) queue.splice(existing, 1);
      const insertAt = playNext ? Math.max(0, state.queueIndex + 1) : queue.length;
      queue.splice(insertAt, 0, track);
      const queueIndex = queue.findIndex((item) => item.key === state.track?.key);
      return { ...state, queue, queueIndex: queueIndex >= 0 ? queueIndex : state.queueIndex };
    });
  }, [playTrack]);

  const togglePlayback = useCallback((track?: WatchTrack, queue?: WatchTrack[]) => {
    const current = playerRef.current;
    if (track && current.track?.key !== track.key) {
      playTrack(track, queue);
      return;
    }
    const audio = audioRef.current;
    if (!audio || !current.track) return;
    if (audio.paused) {
      setPlayer((state) => ({ ...state, playing: true, error: '' }));
      const promise = audio.play();
      if (promise) {
        promise.catch(() => {
          setPlayer((state) => ({
            ...state,
            playing: false,
            error: 'Tap play to start audio.',
          }));
        });
      }
    } else {
      audio.pause();
    }
  }, [playTrack]);

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = seconds;
    setPlayer((state) => ({ ...state, currentTime: seconds }));
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTime = () => {
      setPlayer((state) => ({
        ...state,
        currentTime: audio.currentTime || 0,
        duration: audio.duration || state.duration || state.track?.duration || 0,
      }));
    };
    const onPlay = () => setPlayer((state) => ({ ...state, playing: true, error: '' }));
    const onPause = () => setPlayer((state) => ({ ...state, playing: false }));
    const onEnded = () => playRelative(1);
    const onError = () => {
      setPlayer((state) => ({
        ...state,
        playing: false,
        error: 'Audio could not be loaded.',
      }));
    };

    audio.addEventListener('timeupdate', onTime);
    audio.addEventListener('loadedmetadata', onTime);
    audio.addEventListener('durationchange', onTime);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('error', onError);
    return () => {
      audio.removeEventListener('timeupdate', onTime);
      audio.removeEventListener('loadedmetadata', onTime);
      audio.removeEventListener('durationchange', onTime);
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('error', onError);
    };
  }, [playRelative]);

  return { audioRef, player, playTrack, playRelative, playQueueIndex, addToQueue, togglePlayback, seek };
}

