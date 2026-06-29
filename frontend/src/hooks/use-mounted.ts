import { useState, useEffect } from "react";

/**
 * Hook to track if component is mounted.
 * Useful for SSR-safe animations and avoiding hydration mismatches.
 */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  return mounted;
}

/**
 * Hook to delay mounting for stagger effects.
 * @param delay - Delay in milliseconds before setting mounted to true
 */
export function useDelayedMount(delay: number = 0): boolean {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setMounted(true);
    }, delay);

    return () => clearTimeout(timer);
  }, [delay]);

  return mounted;
}

/**
 * Hook for intersection observer - animate when element comes into view.
 */
export function useInView(
  ref: React.RefObject<HTMLElement>,
  options: IntersectionObserverInit = {}
): boolean {
  const [isInView, setIsInView] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsInView(true);
          observer.disconnect(); // Only trigger once
        }
      },
      { threshold: 0.1, ...options }
    );

    observer.observe(element);

    return () => observer.disconnect();
  }, [ref, options]);

  return isInView;
}
