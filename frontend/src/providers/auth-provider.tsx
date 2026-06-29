"use client";

import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { User, TokenResponse, LoginRequest, RegisterRequest } from "@/lib/auth";
import { API_BASE_URL } from "@/lib/api";
import { getStorageItem, removeStorageItem, setStorageItem } from "@/lib/storage";

interface AuthContextType {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (data: LoginRequest) => Promise<void>;
  register: (data: RegisterRequest) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);
const TOKEN_KEY = "auth_token";
const USER_KEY = "auth_user";

async function readResponseBody(response: Response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return { detail: text || `请求失败 (${response.status})` };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  // 初始化认证状态
  useEffect(() => {
    const initAuth = async () => {
      try {
        const storedToken = getStorageItem(TOKEN_KEY);
        const storedUser = getStorageItem(USER_KEY);
        if (storedToken && storedUser) {
          try {
            const parsedUser = JSON.parse(storedUser) as User;
            setToken(storedToken);
            setUser(parsedUser);
          } catch {
            removeStorageItem(TOKEN_KEY);
            removeStorageItem(USER_KEY);
            setToken(null);
            setUser(null);
            return;
          }
          try {
            const res = await fetch(`${API_BASE_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${storedToken}` } });
            if (res.ok) {
              const userData = await res.json();
              setUser(userData);
              setStorageItem(USER_KEY, JSON.stringify(userData));
            } else {
              removeStorageItem(TOKEN_KEY);
              removeStorageItem(USER_KEY);
              setToken(null);
              setUser(null);
            }
          } catch {}
        }
      } finally {
        setIsLoading(false);
      }
    };
    initAuth();
  }, []);

  // 登录
  const login = useCallback(async (data: LoginRequest) => {
    const res = await fetch(`${API_BASE_URL}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    if (!res.ok) {
      const err = await readResponseBody(res);
      throw new Error(typeof err?.detail === "string" ? err.detail : "登录失败");
    }
    const tokenData: TokenResponse = await res.json();
    setToken(tokenData.access_token);
    setStorageItem(TOKEN_KEY, tokenData.access_token);
    const userRes = await fetch(`${API_BASE_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${tokenData.access_token}` } });
    if (userRes.ok) {
      const userData = await userRes.json();
      setUser(userData);
      setStorageItem(USER_KEY, JSON.stringify(userData));
    }
    router.push("/monitor");
  }, [router]);

  // 注册
  const register = useCallback(async (data: RegisterRequest) => {
    const res = await fetch(`${API_BASE_URL}/api/auth/register`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    if (!res.ok) {
      const err = await readResponseBody(res);
      throw new Error(typeof err?.detail === "string" ? err.detail : "注册失败");
    }
    await login({ username: data.username, password: data.password });
  }, [login]);

  // 登出
  const logout = useCallback(async () => {
    try { if (token) await fetch(`${API_BASE_URL}/api/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }); } catch {}
    setToken(null);
    setUser(null);
    removeStorageItem(TOKEN_KEY);
    removeStorageItem(USER_KEY);
    router.push("/login");
  }, [token, router]);

  // 刷新用户信息
  const refreshUser = useCallback(async () => {
    if (!token) return;
    const res = await fetch(`${API_BASE_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
    if (res.ok) {
      const userData = await res.json();
      setUser(userData);
      setStorageItem(USER_KEY, JSON.stringify(userData));
    }
  }, [token]);

  return <AuthContext.Provider value={{ user, token, isLoading, isAuthenticated: !!token, login, register, logout, refreshUser }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return context;
}
