"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactNode, useState } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () => new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 30 * 1000,    // 数据 30 秒内新鲜
          gcTime: 5 * 60 * 1000,   // 缓存 5 分钟
          retry: 2,                  // 失败重试 2 次
          refetchOnWindowFocus: true, // 窗口聚焦时重新获取
          refetchOnMount: true,
        },
        mutations: {
          retry: 1,
        },
      },
    })
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
