"use client";

import { useAuth } from "@/providers/auth-provider";
import { Sidebar } from "@/components/layout/sidebar";
import { WebSocketProvider } from "@/providers/websocket-provider";
import { AnimationPreference } from "@/components/animation-preference";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { cn } from "@/lib/utils";

interface AppLayoutProps {
  children: React.ReactNode;
}

export function AppLayout({ children }: AppLayoutProps) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push("/login");
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  const isDashboardPage = pathname === "/dashboard";
  const isMonitorPage = pathname === "/monitor";

  return (
    <WebSocketProvider>
      <AnimationPreference />
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />
        <div className="flex-1 flex flex-col h-screen overflow-hidden relative">
          {/* 背景装饰 */}
          <div className="absolute inset-0 -z-10 grid-overlay opacity-[0.03] pointer-events-none" />
          <div className="absolute top-0 right-0 w-64 h-64 -z-10 bg-gradient-to-br from-primary/5 to-transparent blur-3xl" />
          <main
            className={cn(
              "flex-1 overflow-y-auto lg:px-8",
              isDashboardPage
                ? "px-4 pt-4 pb-3 lg:pt-4 lg:pb-3"
                : isMonitorPage
                  ? "px-3 pb-3 pt-[4.5rem] lg:px-4 lg:pb-4 lg:pt-4"
                  : "p-4 pt-20 lg:p-8 lg:pt-8"
            )}
          >
            {children}
          </main>
        </div>
      </div>
    </WebSocketProvider>
  );
}
