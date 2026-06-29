// localStorage 工具，带版本控制和错误处理

const VERSION = "v1";

export const SIDEBAR_STATE_EVENT = "sentinelvision:sidebar-state";

export const STORAGE_KEYS = {
  SIDEBAR_COLLAPSED: `sentinelvision:sidebar-collapsed:${VERSION}`,
  THEME: `sentinelvision:theme:${VERSION}`,
  SETTINGS: `sentinelvision:settings:${VERSION}`,
} as const;

// 安全获取 localStorage
export function getStorageItem(key: string): string | null {
  if (typeof window === "undefined") return null;
  try { return localStorage.getItem(key); } catch (error) { console.warn(`读取 localStorage 失败: ${key}`, error); return null; }
}

// 安全设置 localStorage
export function setStorageItem(key: string, value: string): boolean {
  if (typeof window === "undefined") return false;
  try { localStorage.setItem(key, value); return true; } catch (error) {
    if (error instanceof DOMException) {
      if (error.name === "QuotaExceededError") console.error("localStorage 空间已满");
      else if (error.name === "SecurityError") console.warn("localStorage 被禁用");
    }
    console.warn(`写入 localStorage 失败: ${key}`, error); return false;
  }
}

// 安全删除 localStorage
export function removeStorageItem(key: string): boolean {
  if (typeof window === "undefined") return false;
  try { localStorage.removeItem(key); return true; } catch (error) { console.warn(`删除 localStorage 失败: ${key}`, error); return false; }
}

// 安全获取 JSON
export function getStorageJSON<T>(key: string, defaultValue: T): T {
  const item = getStorageItem(key);
  if (item === null) return defaultValue;
  try { return JSON.parse(item) as T; } catch (error) { console.warn(`解析 JSON 失败: ${key}`, error); return defaultValue; }
}

// 安全设置 JSON
export function setStorageJSON<T>(key: string, value: T): boolean {
  try { return setStorageItem(key, JSON.stringify(value)); } catch (error) { console.warn(`序列化 JSON 失败: ${key}`, error); return false; }
}

// 迁移旧版 storage key
export function migrateStorageKeys(): void {
  if (typeof window === "undefined") return;
  const migrations = [
    { old: "marketwise-sidebar-collapsed", new: STORAGE_KEYS.SIDEBAR_COLLAPSED },
    { old: "marketwise-theme", new: STORAGE_KEYS.THEME },
    { old: "sentinelvision-sidebar-collapsed", new: STORAGE_KEYS.SIDEBAR_COLLAPSED },
    { old: "sentinelvision-theme", new: STORAGE_KEYS.THEME },
  ];
  for (const { old, new: newKey } of migrations) {
    const oldValue = getStorageItem(old);
    if (oldValue !== null) { setStorageItem(newKey, oldValue); removeStorageItem(old); }
  }
}
