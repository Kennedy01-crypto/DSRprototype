"use client";

import { useEffect, useState } from "react";

export function useCurrentTime(): number | null {
  const [currentTime, setCurrentTime] = useState<number | null>(null);

  useEffect(() => {
    const update = () => setCurrentTime(Date.now());
    const initialUpdate = window.setTimeout(update, 0);
    const interval = window.setInterval(update, 60_000);
    return () => {
      window.clearTimeout(initialUpdate);
      window.clearInterval(interval);
    };
  }, []);

  return currentTime;
}
