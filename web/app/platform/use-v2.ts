"use client";

import { useCallback, useEffect, useState } from "react";

type Snapshot<T> = { loader: () => Promise<T>; data: T | null; error: Error | null };

/** Either a fixed interval or a function of the latest value (e.g. poll quickly only while a run is active). */
export type RefreshPolicy<T> = number | ((data: T | null) => number | undefined);

/**
 * Loads a v2 resource for the current `load` callback and optionally re-fetches it while the tab is visible.
 * `load` (and a function-valued `refresh`) must be referentially stable; a new `load` identity re-fetches.
 */
export function useV2Resource<T>(load: () => Promise<T>, refresh?: RefreshPolicy<T>) {
  const [snapshot, setSnapshot] = useState<Snapshot<T> | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let latest: T | null = null;
    const run = async (initial: boolean) => {
      if (cancelled) return;
      if (initial || document.visibilityState === "visible") {
        try {
          const data = await load();
          if (cancelled) return;
          latest = data;
          setSnapshot({ loader: load, data, error: null });
        } catch (reason) {
          if (cancelled) return;
          setSnapshot((current) => ({ loader: load, data: current?.loader === load ? current.data : null, error: reason instanceof Error ? reason : new Error(String(reason)) }));
        }
      }
      const delay = typeof refresh === "function" ? refresh(latest) : refresh;
      if (delay && !cancelled) timer = window.setTimeout(() => void run(false), delay);
    };
    void run(true);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [load, refresh, version]);

  const reload = useCallback(() => {
    setSnapshot(null);
    setVersion((current) => current + 1);
  }, []);

  const refetch = useCallback(() => setVersion((current) => current + 1), []);

  const current = snapshot && snapshot.loader === load ? snapshot : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: current === null,
    /** Clears the current value and fetches again (shows the loading state). */
    reload,
    /** Fetches again while keeping the current value on screen. */
    refresh: refetch,
  };
}
