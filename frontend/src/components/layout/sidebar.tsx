"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Camera,
  Users,
  Cctv,
  Settings,
  ChevronRight,
  ChevronDown,
  Moon,
  Sun,
  Menu,
  X,
  LogOut,
  User,
  FileSearch,
  ClipboardList,
  ScanFace,
} from "lucide-react";
import logoDark from "@/app/logo-dark.png";
import logoNight from "@/app/logo-night.png";
import logoCollapsed from "@/app/logo.png";
import { Button } from "@/components/ui/button";
import {
  STORAGE_KEYS,
  SIDEBAR_STATE_EVENT,
  getStorageItem,
  setStorageItem,
  migrateStorageKeys,
} from "@/lib/storage";
import { useMounted } from "@/hooks/use-mounted";
import { useAuth } from "@/providers/auth-provider";

type SidebarItem = {
  title: string;
  href?: string;
  icon: React.ComponentType<{ className?: string }>;
  children?: { title: string; href: string; icon: React.ComponentType<{ className?: string }> }[];
};

const sidebarItems: SidebarItem[] = [
  {
    title: "实时监控中心",
    href: "/monitor",
    icon: Camera,
  },
  {
    title: "历史数据中心",
    icon: LayoutDashboard,
    children: [
      { title: "可视化数据展示", href: "/dashboard", icon: LayoutDashboard },
      { title: "事件记录", href: "/events", icon: FileSearch },
    ],
  },
  {
    title: "监控管理",
    href: "/cameras",
    icon: Cctv,
  },
  {
    title: "人员管理",
    href: "/persons",
    icon: Users,
  },
  {
    title: "监管配置",
    href: "/supervision",
    icon: ClipboardList,
  },
  {
    title: "人脸测试",
    href: "/face-test",
    icon: ScanFace,
  },
  {
    title: "系统设置",
    href: "/settings",
    icon: Settings,
  },
];

function getActiveParentTitles(pathname: string) {
  const titles = new Set<string>();
  for (const item of sidebarItems) {
    if (item.children?.some((child) => pathname.startsWith(child.href))) {
      titles.add(item.title);
    }
  }
  return titles;
}

function SystemIndicator({ collapsed }: { collapsed: boolean }) {
  if (collapsed) {
    return (
      <motion.div
        className="mt-4 w-full p-2 rounded-xl border bg-success/10 border-success/20 flex items-center justify-center shadow-soft-sm"
      >
        <div className="relative">
          <motion.div
            animate={{ scale: [1, 1.3, 1], opacity: [1, 0.6, 1] }}
            transition={{ duration: 2, repeat: Infinity }}
            className="w-2 h-2 rounded-full bg-success"
          />
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-4 space-y-2"
    >
      <div className="p-3 rounded-xl border bg-success/10 border-success/20 shadow-soft-sm">
        <div className="flex items-center gap-2">
          <div className="relative">
            <motion.div
              animate={{ scale: [1, 1.3, 1], opacity: [1, 0.6, 1] }}
              transition={{ duration: 2, repeat: Infinity }}
              className="w-2 h-2 rounded-full bg-success"
            />
          </div>
          <span className="text-xs font-medium text-success">
            系统运行正常
          </span>
        </div>
      </div>
    </motion.div>
  );
}

function UserSection({ collapsed }: { collapsed: boolean }) {
  const { user, logout, isAuthenticated } = useAuth();

  if (!isAuthenticated || !user) {
    return null;
  }

  return (
    <motion.div whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }}>
      <div
        className={cn(
          "flex items-center gap-3 p-3 rounded-xl bg-muted/50 border border-border/50 mb-1.5",
          collapsed && "justify-center p-2"
        )}
      >
        {!collapsed && (
          <div className="w-8 h-8 rounded-full bg-gradient-primary flex items-center justify-center shrink-0">
            <User className="w-4 h-4 text-white" />
          </div>
        )}
        <AnimatePresence>
          {!collapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 min-w-0"
            >
              <p className="text-sm font-medium truncate">
                {user.full_name || user.username}
              </p>
              <p className="text-xs text-muted-foreground truncate">
                {user.role === "admin" ? "管理员" : "用户"}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
        <Button
          variant="ghost"
          size="icon"
          onClick={logout}
          className={cn(
            "h-8 w-8 shrink-0 rounded-lg hover:bg-destructive/10 hover:text-destructive",
            collapsed && "h-8 w-8"
          )}
          title="登出"
        >
          <LogOut className="w-4 h-4" />
        </Button>
      </div>
    </motion.div>
  );
}

function MobileSidebar({
  isOpen,
  onClose,
  pathname,
  isDark,
  onToggleTheme,
}: {
  isOpen: boolean;
  onClose: () => void;
  pathname: string;
  isDark: boolean;
  onToggleTheme: () => void;
}) {
  const [mobileExpandedParents, setMobileExpandedParents] = useState<Set<string>>(() => {
    for (const item of sidebarItems) {
      if (item.children?.some((c) => pathname.startsWith(c.href))) {
        return new Set([item.title]);
      }
    }
    return new Set();
  });
  const toggleMobileParent = (title: string) => {
    setMobileExpandedParents((prev) => {
      const next = new Set(prev);
      if (next.has(title)) {
        next.delete(title);
      } else {
        next.add(title);
      }
      return next;
    });
  };
  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm lg:hidden"
            onClick={onClose}
          />
          <motion.div
            initial={{ x: "-100%" }}
            animate={{ x: 0 }}
            exit={{ x: "-100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 300 }}
            className="fixed left-0 top-0 bottom-0 z-50 w-72 bg-sidebar border-r border-border lg:hidden shadow-soft-xl"
          >
            <div className="flex flex-col h-full">
              <div className="p-4 flex items-center justify-between border-b border-border">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 items-center">
                    <Image
                      src={isDark ? logoNight : logoDark}
                      alt="Logo"
                      className="h-9 w-auto object-contain"
                      priority
                    />
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={onClose}
                  className="h-10 w-10 rounded-xl border border-border/50 hover:bg-muted"
                >
                  <X className="h-5 w-5" />
                </Button>
              </div>

              <nav className="flex-1 p-4 space-y-1">
                {sidebarItems.map((item, index) => {
                  const hasChildren = item.children && item.children.length > 0;
                  const isChildActive = hasChildren && item.children!.some((child) => pathname.startsWith(child.href));
                  const isDirectActive = !hasChildren && pathname === item.href;
                  const isExpanded = hasChildren && mobileExpandedParents.has(item.title);

                  if (hasChildren) {
                    return (
                      <motion.div
                        key={item.title}
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        transition={{ delay: index * 0.05 }}
                      >
                        <Button
                          variant="ghost"
                          onClick={() => toggleMobileParent(item.title)}
                          className={cn(
                            "w-full justify-start gap-3 h-12 relative group font-medium rounded-xl text-sm",
                            "text-muted-foreground hover:bg-muted hover:text-foreground",
                            (isChildActive || isExpanded) && "bg-muted text-foreground"
                          )}
                        >
                          <item.icon
                            className={cn(
                              "w-5 h-5 shrink-0",
                              (isChildActive || isExpanded) ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
                            )}
                          />
                          <span className="flex-1 text-left">{item.title}</span>
                          <ChevronDown
                            className={cn(
                              "w-3.5 h-3.5 transition-transform",
                              (isChildActive || isExpanded) ? "text-foreground" : "text-muted-foreground",
                              isExpanded && "rotate-180"
                            )}
                          />
                        </Button>
                        <AnimatePresence>
                          {isExpanded && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: "auto", opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              className="overflow-hidden ml-4 space-y-0.5 mt-0.5 border-l border-border/50 pl-2"
                            >
                              {item.children!.map((child) => {
                                const isActive = pathname === child.href;
                                return (
                                  <Link key={child.href} href={child.href} onClick={onClose}>
                                    <Button
                                      variant="ghost"
                                      className={cn(
                                        "w-full justify-start gap-2.5 h-10 font-normal rounded-lg text-xs",
                                        isActive
                                          ? "bg-primary/10 text-primary"
                                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                      )}
                                    >
                                      <child.icon className={cn("w-4 h-4", isActive ? "text-primary" : "")} />
                                      <span>{child.title}</span>
                                    </Button>
                                  </Link>
                                );
                              })}
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </motion.div>
                    );
                  }

                  return (
                    <motion.div
                      key={item.title}
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: index * 0.05 }}
                    >
                      <Link href={item.href!} onClick={onClose}>
                        <Button
                          variant="ghost"
                          className={cn(
                            "w-full justify-start gap-3 h-12 relative group font-medium rounded-xl text-sm",
                            isDirectActive
                              ? "bg-gradient-primary text-white shadow-soft-md"
                              : "text-muted-foreground hover:bg-muted hover:text-foreground"
                          )}
                        >
                          <item.icon
                            className={cn(
                              "w-5 h-5 shrink-0",
                              isDirectActive ? "text-white" : "text-muted-foreground group-hover:text-foreground"
                            )}
                          />
                          <span>{item.title}</span>
                        </Button>
                      </Link>
                    </motion.div>
                  );
                })}
              </nav>

              <div className="p-4 border-t border-border space-y-2">
                <Button
                  variant="ghost"
                  onClick={onToggleTheme}
                  className="w-full justify-start gap-3 h-12 font-medium rounded-xl hover:bg-muted"
                >
                  {isDark ? (
                    <Sun className="w-5 h-5 text-amber-500" />
                  ) : (
                    <Moon className="w-5 h-5 text-slate-500" />
                  )}
                  <span>{isDark ? "浅色模式" : "深色模式"}</span>
                </Button>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

export function MobileHeader({
  onOpenSidebar,
}: {
  onOpenSidebar: () => void;
}) {
  return (
    <div className="lg:hidden fixed top-0 left-0 right-0 z-30 h-16 bg-sidebar/95 backdrop-blur-lg border-b border-border flex items-center justify-between px-4">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          onClick={onOpenSidebar}
          className="h-10 w-10 rounded-xl border border-border/50 hover:bg-muted"
        >
          <Menu className="h-5 w-5" />
        </Button>
        <div className="flex h-10 items-center">
          <Image
            src={logoDark}
            alt="Logo"
            className="h-8 w-auto object-contain dark:hidden"
            priority
          />
          <Image
            src={logoNight}
            alt="Logo"
            className="hidden h-8 w-auto object-contain dark:block"
            priority
          />
        </div>
      </div>
    </div>
  );
}

export function Sidebar() {
  const pathname = usePathname();

  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window !== "undefined") {
      migrateStorageKeys();
      const stored = getStorageItem(STORAGE_KEYS.SIDEBAR_COLLAPSED);
      return stored === "true";
    }
    return false;
  });

  const mounted = useMounted();

  const [isDark, setIsDark] = useState(() => {
    if (typeof window !== "undefined") {
      return document.documentElement.classList.contains("dark");
    }
    return false;
  });

  const [mobileOpenPathname, setMobileOpenPathname] = useState<string | null>(null);
  const [expandedParents, setExpandedParents] = useState<Set<string>>(() => {
    return getActiveParentTitles(pathname);
  });
  const activeParentTitles = useMemo(() => getActiveParentTitles(pathname), [pathname]);
  const visibleExpandedParents = useMemo(() => {
    const next = new Set(expandedParents);
    activeParentTitles.forEach((title) => next.add(title));
    return next;
  }, [activeParentTitles, expandedParents]);
  const isMobileSidebarOpen = mobileOpenPathname === pathname;

  const toggleParent = (title: string) => {
    setExpandedParents((prev) => {
      const next = new Set(prev);
      if (next.has(title)) {
        next.delete(title);
      } else {
        next.add(title);
      }
      return next;
    });
  };

  const handleToggleCollapse = () => {
    const newState = !collapsed;
    setCollapsed(newState);
    setStorageItem(STORAGE_KEYS.SIDEBAR_COLLAPSED, String(newState));
    if (typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent(SIDEBAR_STATE_EVENT, {
          detail: { collapsed: newState },
        })
      );
    }
  };

  const handleToggleTheme = () => {
    const newIsDark = !isDark;
    setIsDark(newIsDark);
    const newTheme = newIsDark ? "dark" : "light";
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(newTheme);
    setStorageItem(STORAGE_KEYS.THEME, newTheme);
  };

  return (
    <>
      <MobileHeader onOpenSidebar={() => setMobileOpenPathname(pathname)} />
      <MobileSidebar
        isOpen={isMobileSidebarOpen}
        onClose={() => setMobileOpenPathname(null)}
        pathname={pathname}
        isDark={isDark}
        onToggleTheme={handleToggleTheme}
      />

      <motion.div
        initial={false}
        animate={{ width: collapsed ? 76 : 320 }}
        transition={{ duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
        className={cn(
          "hidden lg:flex flex-col h-screen bg-sidebar border-r border-border relative"
        )}
      >
        <motion.button
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.95 }}
          onClick={handleToggleCollapse}
          className="absolute -right-3 top-8 z-10 flex h-6 w-6 items-center justify-center rounded-full bg-gradient-primary text-white shadow-soft-md hover:shadow-soft-lg transition-shadow"
          aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
        >
          <motion.div
            animate={{ rotate: collapsed ? 0 : 180 }}
            transition={{ duration: 0.3 }}
          >
            <ChevronRight className="h-3 w-3 font-bold" />
          </motion.div>
        </motion.button>

        <div className={cn("p-4 border-b border-border", collapsed ? "px-3" : "p-4")}>
          <div
            className={cn(
              "flex items-center gap-3 transition-all duration-300",
              collapsed && "justify-center"
            )}
          >
            <div className={cn("flex items-center shrink-0", collapsed ? "justify-center" : "")}>
              <Image
                src={collapsed ? logoCollapsed : (isDark ? logoNight : logoDark)}
                alt="Logo"
                className={cn(
                  "w-auto object-contain transition-all duration-300",
                  collapsed ? "h-9" : "h-10"
                )}
                priority
              />
            </div>
            <AnimatePresence>
              {!collapsed && (
                <motion.div
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -10 }}
                  transition={{ duration: 0.2 }}
                  className="hidden"
                />
              )}
            </AnimatePresence>
          </div>

          <nav className={cn("space-y-1", collapsed ? "mt-6" : "mt-6")}>
            {sidebarItems.map((item) => {
              const hasChildren = item.children && item.children.length > 0;
              const isChildActive = hasChildren && item.children!.some((child) => pathname.startsWith(child.href));
              const isDirectActive = !hasChildren && pathname === item.href;
              const isExpanded = hasChildren && visibleExpandedParents.has(item.title);

              if (hasChildren) {
                return (
                  <div key={item.title}>
                    <motion.div whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }}>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          if (collapsed) {
                            handleToggleCollapse();
                          }
                          toggleParent(item.title);
                        }}
                        className={cn(
                          "w-full justify-start gap-3 transition-all duration-200 relative group h-11 font-medium rounded-xl text-sm",
                          collapsed && "justify-center px-2",
                          "text-muted-foreground hover:bg-muted hover:text-foreground",
                          (isChildActive || isExpanded) && "bg-muted text-foreground"
                        )}
                      >
                        <item.icon
                          className={cn(
                            "w-5 h-5 shrink-0 transition-colors",
                            (isChildActive || isExpanded) ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"
                          )}
                        />
                        <AnimatePresence>
                          {!collapsed && (
                            <motion.span
                              initial={{ opacity: 0 }}
                              animate={{ opacity: 1 }}
                              exit={{ opacity: 0 }}
                              transition={{ duration: 0.15 }}
                              className="flex-1 text-left"
                            >
                              {item.title}
                            </motion.span>
                          )}
                        </AnimatePresence>
                        {!collapsed && (
                          <motion.div
                            animate={{ rotate: isExpanded ? 180 : 0 }}
                            transition={{ duration: 0.2 }}
                            className="shrink-0"
                          >
                            <ChevronDown className={cn(
                              "w-3.5 h-3.5",
                              (isChildActive || isExpanded) ? "text-foreground" : "text-muted-foreground"
                            )} />
                          </motion.div>
                        )}
                      </Button>
                    </motion.div>
                    <AnimatePresence>
                      {!collapsed && isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="overflow-hidden ml-4 space-y-0.5 mt-0.5 border-l border-border/50 pl-2"
                        >
                          {item.children!.map((child) => {
                            const isActive = pathname === child.href;
                            return (
                              <Link key={child.href} href={child.href}>
                                <motion.div whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }}>
                                  <Button
                                    variant="ghost"
                                    className={cn(
                                      "w-full justify-start gap-2.5 h-9 font-normal rounded-lg text-xs",
                                      isActive
                                        ? "bg-primary/10 text-primary"
                                        : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                    )}
                                  >
                                    <child.icon className={cn(
                                      "w-4 h-4 shrink-0",
                                      isActive ? "text-primary" : "text-muted-foreground"
                                    )} />
                                    <span>{child.title}</span>
                                  </Button>
                                </motion.div>
                              </Link>
                            );
                          })}
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                );
              }

              return (
                <Link key={item.title} href={item.href!}>
                  <motion.div whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }}>
                    <Button
                      variant="ghost"
                      className={cn(
                        "w-full justify-start gap-3 transition-all duration-200 relative group h-11 font-medium rounded-xl text-sm",
                        collapsed && "justify-center px-2",
                        isDirectActive
                          ? "bg-gradient-primary text-white shadow-soft-md"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                      )}
                    >
                      <item.icon
                        className={cn(
                          "w-5 h-5 shrink-0 transition-colors",
                          isDirectActive ? "text-white" : "text-muted-foreground group-hover:text-foreground"
                        )}
                      />
                      <AnimatePresence>
                        {!collapsed && (
                          <motion.span
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.15 }}
                          >
                            {item.title}
                          </motion.span>
                        )}
                      </AnimatePresence>
                    </Button>
                  </motion.div>
                </Link>
              );
            })}
          </nav>
        </div>

        <div
          className={cn(
            "mt-auto border-t border-border",
            collapsed ? "p-2" : "p-4"
          )}
        >
          {mounted && (
            <motion.div whileHover={{ x: 2 }} whileTap={{ scale: 0.98 }}>
              <Button
                variant="ghost"
                onClick={handleToggleTheme}
                className={cn(
                  "w-full justify-start gap-3 text-muted-foreground hover:text-foreground hover:bg-muted transition-all duration-200 mb-1.5 h-11 font-medium rounded-xl text-sm",
                  collapsed && "justify-center px-2"
                )}
              >
                <motion.div
                  animate={{ rotate: isDark ? 360 : 0 }}
                  transition={{ duration: 0.5 }}
                >
                  {isDark ? (
                    <Sun className="w-5 h-5 shrink-0 text-amber-500" />
                  ) : (
                    <Moon className="w-5 h-5 shrink-0 text-slate-500" />
                  )}
                </motion.div>
                <AnimatePresence>
                  {!collapsed && (
                    <motion.span
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                    >
                      {isDark ? "浅色模式" : "深色模式"}
                    </motion.span>
                  )}
                </AnimatePresence>
              </Button>
            </motion.div>
          )}

          <UserSection collapsed={collapsed} />
          <SystemIndicator collapsed={collapsed} />
        </div>
      </motion.div>
    </>
  );
}
