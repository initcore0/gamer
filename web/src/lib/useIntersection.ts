import { useEffect, useRef } from "react";

/**
 * Calls `onIntersect` when the returned ref's element scrolls into view — the
 * infinite-scroll trigger. `enabled` gates it (e.g. only while more pages exist
 * and none is currently loading), so a fired callback never double-fetches.
 */
export function useIntersection(
  onIntersect: () => void,
  enabled: boolean,
): (node: HTMLElement | null) => void {
  const callbackRef = useRef(onIntersect);
  callbackRef.current = onIntersect;
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => () => observerRef.current?.disconnect(), []);

  return (node: HTMLElement | null) => {
    observerRef.current?.disconnect();
    if (!node || !enabled) return;
    observerRef.current = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) callbackRef.current();
    });
    observerRef.current.observe(node);
  };
}
