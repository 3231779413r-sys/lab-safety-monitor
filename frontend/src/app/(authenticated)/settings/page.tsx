"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/page-header";
import { MotionWrapper } from "@/components/motion-wrapper";
import {
  Settings,
  Palette,
  Bell,
  Monitor,
  Moon,
  Sun,
  Volume2,
  VolumeX,
  Eye,
  EyeOff,
  RefreshCw,
  Save,
  Zap,
  Shield,
  Clock,
  Server,
} from "lucide-react";
import { toast } from "sonner";
import {
  STORAGE_KEYS,
  getStorageItem,
  setStorageItem,
  removeStorageItem,
  getStorageJSON,
  setStorageJSON,
  migrateStorageKeys,
} from "@/lib/storage";

interface SettingToggleProps {
  label: string;
  description: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  icon: React.ReactNode;
}

interface SettingsData {
  soundEnabled?: boolean;
  desktopNotifications?: boolean;
  violationAlerts?: boolean;
  autoRefresh?: boolean;
  refreshInterval?: string;
  showAnimations?: boolean;
}

function SettingToggle({ label, description, enabled, onChange, icon }: SettingToggleProps) {
  return (
    <motion.div
      whileHover={{ x: 4 }}
      className="flex items-center justify-between p-4 rounded-xl border border-border/50 hover:border-border transition-all duration-200 bg-card/50"
    >
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10">
          {icon}
        </div>
        <div>
          <p className="font-medium text-sm">{label}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        </div>
      </div>
      <motion.button
        whileTap={{ scale: 0.95 }}
        onClick={() => onChange(!enabled)}
        className={`relative w-12 h-7 rounded-full transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${enabled ? "bg-primary" : "bg-muted"
          }`}
        role="switch"
        aria-checked={enabled}
        aria-label={label}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            onChange(!enabled);
          }
        }}
      >
        <motion.div
          animate={{ x: enabled ? 22 : 4 }}
          transition={{ type: "spring", stiffness: 500, damping: 30 }}
          className="absolute top-1 w-5 h-5 rounded-full bg-white shadow-md"
        />
      </motion.button>
    </motion.div>
  );
}

interface SettingSelectProps {
  label: string;
  description: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  icon: React.ReactNode;
}

function SettingSelect({ label, description, value, options, onChange, icon }: SettingSelectProps) {
  return (
    <motion.div
      whileHover={{ x: 4 }}
      className="flex items-center justify-between p-4 rounded-xl border border-border/50 hover:border-border transition-all duration-200 bg-card/50"
    >
      <div className="flex items-center gap-4">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10">
          {icon}
        </div>
        <div>
          <p className="font-medium text-sm">{label}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        </div>
      </div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="px-3 py-1.5 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </motion.div>
  );
}

export default function SettingsPage() {
  // Lazy state initialization with safe defaults
  const [theme, setTheme] = useState<"light" | "dark" | "system">(() => {
    if (typeof window !== "undefined") {
      migrateStorageKeys();
      const savedTheme = getStorageItem(STORAGE_KEYS.THEME);
      if (savedTheme === "light" || savedTheme === "dark") {
        return savedTheme;
      }
    }
    return "system";
  });

  // Notification settings with lazy initialization
  const [soundEnabled, setSoundEnabled] = useState(() => {
    if (typeof window !== "undefined") {
      const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
      return settings.soundEnabled ?? true;
    }
    return true;
  });

  const [desktopNotifications, setDesktopNotifications] = useState(() => {
    if (typeof window !== "undefined") {
      const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
      return settings.desktopNotifications ?? true;
    }
    return true;
  });

  const [violationAlerts, setViolationAlerts] = useState(() => {
    if (typeof window !== "undefined") {
      const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
      return settings.violationAlerts ?? true;
    }
    return true;
  });

  // Display settings with lazy initialization
  const [autoRefresh, setAutoRefresh] = useState(() => {
    if (typeof window !== "undefined") {
      const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
      return settings.autoRefresh ?? true;
    }
    return true;
  });

  const [refreshInterval, setRefreshInterval] = useState(() => {
    if (typeof window !== "undefined") {
      const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
      return settings.refreshInterval ?? "30";
    }
    return "30";
  });

  const [showAnimations, setShowAnimations] = useState(() => {
    if (typeof window !== "undefined") {
      const settings = getStorageJSON<SettingsData>(STORAGE_KEYS.SETTINGS, {});
      return settings.showAnimations ?? true;
    }
    return true;
  });

  const handleThemeChange = (newTheme: "light" | "dark" | "system") => {
    setTheme(newTheme);

    if (newTheme === "system") {
      const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.classList.remove("light", "dark");
      document.documentElement.classList.add(systemDark ? "dark" : "light");
      removeStorageItem(STORAGE_KEYS.THEME);
    } else {
      document.documentElement.classList.remove("light", "dark");
      document.documentElement.classList.add(newTheme);
      setStorageItem(STORAGE_KEYS.THEME, newTheme);
    }

    toast.success("主题已更新", {
      description: `已切换到${newTheme === "light" ? "浅色" : newTheme === "dark" ? "深色" : "系统"}模式`,
    });
  };

  const handleSaveSettings = () => {
    const settings = {
      soundEnabled,
      desktopNotifications,
      violationAlerts,
      autoRefresh,
      refreshInterval,
      showAnimations,
    };

    const success = setStorageJSON(STORAGE_KEYS.SETTINGS, settings);

    if (success) {
      toast.success("设置已保存", {
        description: "您的偏好设置已成功保存",
        icon: <Save className="w-4 h-4" />,
      });
    } else {
      toast.error("保存设置失败", {
        description: "无法保存偏好设置，存储可能被禁用。",
      });
    }
  };

  const handleResetSettings = () => {
    setSoundEnabled(true);
    setDesktopNotifications(true);
    setViolationAlerts(true);
    setAutoRefresh(true);
    setRefreshInterval("30");
    setShowAnimations(true);
    removeStorageItem(STORAGE_KEYS.SETTINGS);

    toast.info("设置已重置", {
      description: "所有设置已恢复为默认值",
    });
  };

  return (
    <MotionWrapper className="space-y-8">
      {/* Header */}
      <PageHeader
        title="设置"
        description="自定义您的使用体验"
        action={
          <div className="flex items-center gap-2">
            <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
              <Button variant="outline" size="sm" onClick={handleResetSettings}>
                <RefreshCw className="w-4 h-4 mr-2" />
                重置
              </Button>
            </motion.div>
            <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
              <Button size="sm" onClick={handleSaveSettings}>
                <Save className="w-4 h-4 mr-2" />
                保存更改
              </Button>
            </motion.div>
          </div>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Appearance Section */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card variant="glass" hover>
            <CardHeader>
              <CardTitle className="flex items-center gap-3">
                <motion.div
                  whileHover={{ scale: 1.1, rotate: 10 }}
                  className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10"
                >
                  <Palette className="w-5 h-5 text-primary" />
                </motion.div>
                <div>
                  <span>外观</span>
                  <CardDescription className="mt-0.5">
                    自定义 SentinelVision 的外观
                  </CardDescription>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Theme Selection */}
              <div className="space-y-3">
                <p className="text-sm font-medium text-muted-foreground">主题</p>
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { value: "light", label: "浅色", icon: Sun },
                    { value: "dark", label: "深色", icon: Moon },
                    { value: "system", label: "跟随系统", icon: Monitor },
                  ].map(({ value, label, icon: Icon }) => (
                    <motion.button
                      key={value}
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                      onClick={() => handleThemeChange(value as "light" | "dark" | "system")}
                      className={`flex flex-col items-center gap-2 p-4 rounded-xl border transition-all duration-200 ${theme === value
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border hover:border-primary/50"
                        }`}
                    >
                      <Icon className="w-5 h-5" />
                      <span className="text-xs font-medium">{label}</span>
                    </motion.button>
                  ))}
                </div>
              </div>

              <SettingToggle
                label="显示动画"
                description="启用流畅的过渡和效果"
                enabled={showAnimations}
                onChange={setShowAnimations}
                icon={<Zap className="w-5 h-5 text-primary" />}
              />
            </CardContent>
          </Card>
        </motion.div>

        {/* Notifications Section */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.3 }}
        >
          <Card variant="glass" hover>
            <CardHeader>
              <CardTitle className="flex items-center gap-3">
                <motion.div
                  whileHover={{ scale: 1.1, rotate: -10 }}
                  className="flex items-center justify-center w-10 h-10 rounded-xl bg-warning/10"
                >
                  <Bell className="w-5 h-5 text-warning" />
                </motion.div>
                <div>
                  <span>通知</span>
                  <CardDescription className="mt-0.5">
                    配置告警偏好
                  </CardDescription>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <SettingToggle
                label="违规告警"
                description="检测到违规时通知您"
                enabled={violationAlerts}
                onChange={setViolationAlerts}
                icon={<Shield className="w-5 h-5 text-danger" />}
              />
              <SettingToggle
                label="声音通知"
                description="为重要告警播放声音"
                enabled={soundEnabled}
                onChange={setSoundEnabled}
                icon={soundEnabled ? <Volume2 className="w-5 h-5 text-info" /> : <VolumeX className="w-5 h-5 text-muted-foreground" />}
              />
              <SettingToggle
                label="桌面通知"
                description="显示浏览器通知"
                enabled={desktopNotifications}
                onChange={setDesktopNotifications}
                icon={desktopNotifications ? <Eye className="w-5 h-5 text-success" /> : <EyeOff className="w-5 h-5 text-muted-foreground" />}
              />
            </CardContent>
          </Card>
        </motion.div>

        {/* Data & Performance Section */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <Card variant="glass" hover>
            <CardHeader>
              <CardTitle className="flex items-center gap-3">
                <motion.div
                  whileHover={{ scale: 1.1 }}
                  className="flex items-center justify-center w-10 h-10 rounded-xl bg-success/10"
                >
                  <Server className="w-5 h-5 text-success" />
                </motion.div>
                <div>
                  <span>数据与性能</span>
                  <CardDescription className="mt-0.5">
                    管理数据刷新和性能
                  </CardDescription>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <SettingToggle
                label="自动刷新数据"
                description="自动刷新仪表板数据"
                enabled={autoRefresh}
                onChange={setAutoRefresh}
                icon={<RefreshCw className="w-5 h-5 text-primary" />}
              />
              <SettingSelect
                label="刷新间隔"
                description="数据刷新频率"
                value={refreshInterval}
                onChange={setRefreshInterval}
                options={[
                  { value: "10", label: "10 秒" },
                  { value: "30", label: "30 秒" },
                  { value: "60", label: "1 分钟" },
                  { value: "300", label: "5 分钟" },
                ]}
                icon={<Clock className="w-5 h-5 text-info" />}
              />
            </CardContent>
          </Card>
        </motion.div>

        {/* About Section */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
        >
          <Card variant="glass" hover>
            <CardHeader>
              <CardTitle className="flex items-center gap-3">
                <motion.div
                  whileHover={{ scale: 1.1, rotate: 360 }}
                  transition={{ duration: 0.5 }}
                  className="flex items-center justify-center w-10 h-10 rounded-xl bg-info/10"
                >
                  <Settings className="w-5 h-5 text-info" />
                </motion.div>
                <div>
                  <span>关于</span>
                  <CardDescription className="mt-0.5">
                    系统信息
                  </CardDescription>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between p-4 rounded-xl bg-muted/30">
                <div>
                  <p className="font-medium text-sm">实验室安全</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    AI 驱动的安全合规监控
                  </p>
                </div>
                <Badge variant="info">v1.0.0</Badge>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 rounded-xl bg-muted/30 text-center">
                  <p className="text-2xl font-bold text-primary">YOLOv11</p>
                  <p className="text-xs text-muted-foreground">PPE 检测</p>
                </div>
                <div className="p-3 rounded-xl bg-muted/30 text-center">
                  <p className="text-2xl font-bold text-success">SAM3</p>
                  <p className="text-xs text-muted-foreground">分割模型</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </MotionWrapper>
  );
}