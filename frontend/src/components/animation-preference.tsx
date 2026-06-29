"use client";

import { useEffect } from "react";
import {
  STORAGE_KEYS,
  getStorageJSON,
} from "@/lib/storage";

interface SettingsData {
  showAnimations?: boolean;
}

export function AnimationPreference() {
  useEffect(() => {
    const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
    if (settings.showAnimations === false) {
      document.documentElement.classList.add("reduce-motion");
    } else {
      document.documentElement.classList.remove("reduce-motion");
    }
  }, []);

  return null;
}
