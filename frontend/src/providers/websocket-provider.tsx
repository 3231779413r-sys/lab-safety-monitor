"use client";

import React, { createContext, useContext, useEffect, useState, useRef } from "react";

// WebSocket 消息类型
export type AlertMessage = {
  type: "violation" | "violation_update" | "system";
  title: string;
  message: string;
  timestamp: string;
  severity: "info" | "warning" | "error";
  event_id?: string;
  person_id?: string;
  person_name?: string;
  missing_ppe?: string[];
  violation_labels?: string[];
  snapshot_filename?: string;
  snapshot_path?: string;
  snapshot_url?: string;
  video_url?: string;
  camera_id?: string;
  camera_ids?: string[];
  camera_name?: string;
};

type WebSocketContextType = {
  isConnected: boolean;
  lastMessage: AlertMessage | null;
};

const WebSocketContext = createContext<WebSocketContextType>({ isConnected: false, lastMessage: null });

export function useWebSocket() {
  return useContext(WebSocketContext);
}

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<AlertMessage | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const connect = () => {
    if (typeof window === "undefined") return;
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
    const normalizedBase = apiUrl
      ? apiUrl.replace(/\/$/, "").replace(/^http/, "ws")
      : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
    const wsUrl = `${normalizedBase}/api/ws`;
    try {
      const ws = new WebSocket(wsUrl);
      socketRef.current = ws;
      ws.onopen = () => { console.log("WebSocket 已连接"); setIsConnected(true); reconnectAttemptRef.current = 0; };
      ws.onmessage = (event) => { try { setLastMessage(JSON.parse(event.data)); } catch { console.error("解析 WS 消息失败"); } };
      ws.onclose = () => { console.log("WebSocket 已断开"); setIsConnected(false); scheduleReconnect(); };
      ws.onerror = () => { console.error("WebSocket 连接失败，请确认后端服务已启动"); };
    } catch (err) { console.error("WebSocket 连接失败", err); scheduleReconnect(); }
  };

  const scheduleReconnect = () => {
    if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
    const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 30000);
    reconnectAttemptRef.current += 1;
    console.log(`WebSocket ${delay}ms 后重连 (第 ${reconnectAttemptRef.current} 次)`);
    reconnectTimeoutRef.current = setTimeout(() => { connect(); }, delay);
  };

  useEffect(() => {
    connect();
    return () => { if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current); if (socketRef.current) socketRef.current.close(); };
  }, []);

  return <WebSocketContext.Provider value={{ isConnected, lastMessage }}>{children}</WebSocketContext.Provider>;
}
