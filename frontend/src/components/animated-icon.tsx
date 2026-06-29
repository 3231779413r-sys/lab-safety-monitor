"use client";

import { motion, Variants } from "framer-motion";
import { cn } from "@/lib/utils";
import { ReactNode } from "react";
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  Loader2,
  Shield,
  Activity,
  Zap,
} from "lucide-react";

type IconVariant = "success" | "warning" | "danger" | "info" | "loading" | "shield" | "activity" | "zap";

interface AnimatedIconProps {
  variant: IconVariant;
  size?: "sm" | "md" | "lg" | "xl";
  animate?: boolean;
  className?: string;
}

const iconMap: Record<IconVariant, typeof CheckCircle2> = {
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  info: Info,
  loading: Loader2,
  shield: Shield,
  activity: Activity,
  zap: Zap,
};

const colorMap: Record<IconVariant, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  loading: "text-primary",
  shield: "text-success",
  activity: "text-primary",
  zap: "text-warning",
};

const bgColorMap: Record<IconVariant, string> = {
  success: "bg-success/10",
  warning: "bg-warning/10",
  danger: "bg-danger/10",
  info: "bg-info/10",
  loading: "bg-primary/10",
  shield: "bg-success/10",
  activity: "bg-primary/10",
  zap: "bg-warning/10",
};

const sizeMap = {
  sm: { container: "w-8 h-8", icon: "w-4 h-4" },
  md: { container: "w-10 h-10", icon: "w-5 h-5" },
  lg: { container: "w-12 h-12", icon: "w-6 h-6" },
  xl: { container: "w-16 h-16", icon: "w-8 h-8" },
};

const pulseVariants: Variants = {
  initial: { scale: 1 },
  animate: {
    scale: [1, 1.1, 1],
    transition: {
      duration: 2,
      ease: "easeInOut",
      repeat: Infinity,
    },
  },
};

const spinVariants: Variants = {
  initial: { rotate: 0 },
  animate: {
    rotate: 360,
    transition: {
      duration: 1,
      ease: "linear",
      repeat: Infinity,
    },
  },
};

const bounceVariants: Variants = {
  initial: { y: 0 },
  animate: {
    y: [0, -5, 0],
    transition: {
      duration: 1.5,
      ease: "easeInOut",
      repeat: Infinity,
    },
  },
};

/**
 * Animated icon component with various states and animations.
 */
export function AnimatedIcon({
  variant,
  size = "md",
  animate = true,
  className,
}: AnimatedIconProps) {
  const Icon = iconMap[variant];
  const iconColor = colorMap[variant];
  const bgColor = bgColorMap[variant];
  const sizes = sizeMap[size];

  const getVariants = () => {
    if (!animate) return {};
    if (variant === "loading") return spinVariants;
    if (variant === "activity" || variant === "zap") return bounceVariants;
    return pulseVariants;
  };

  return (
    <motion.div
      variants={getVariants()}
      initial="initial"
      animate={animate ? "animate" : "initial"}
      className={cn(
        "flex items-center justify-center rounded-xl transition-colors",
        sizes.container,
        bgColor,
        className
      )}
    >
      <Icon className={cn(sizes.icon, iconColor)} />
    </motion.div>
  );
}

/**
 * Status dot indicator with optional pulse animation.
 */
interface StatusDotProps {
  status: "online" | "offline" | "warning" | "busy";
  pulse?: boolean;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const dotColorMap: Record<StatusDotProps["status"], string> = {
  online: "bg-success",
  offline: "bg-muted-foreground",
  warning: "bg-warning",
  busy: "bg-danger",
};

const dotSizeMap = {
  sm: "h-2 w-2",
  md: "h-2.5 w-2.5",
  lg: "h-3 w-3",
};

export function StatusDot({
  status,
  pulse = true,
  size = "md",
  className,
}: StatusDotProps) {
  const dotColor = dotColorMap[status];
  const dotSize = dotSizeMap[size];

  return (
    <span className={cn("relative flex", dotSize, className)}>
      {pulse && (status === "online" || status === "busy") && (
        <motion.span
          animate={{ scale: [1, 1.5, 1], opacity: [0.75, 0, 0.75] }}
          transition={{ duration: 1.5, repeat: Infinity }}
          className={cn(
            "absolute inline-flex h-full w-full rounded-full",
            dotColor
          )}
        />
      )}
      <span className={cn("relative inline-flex rounded-full", dotSize, dotColor)} />
    </span>
  );
}

/**
 * Icon with background wrapper.
 */
interface IconWrapperProps {
  children: ReactNode;
  variant?: "default" | "success" | "warning" | "danger" | "info" | "primary";
  size?: "sm" | "md" | "lg";
  className?: string;
}

const wrapperBgMap: Record<NonNullable<IconWrapperProps["variant"]>, string> = {
  default: "bg-muted",
  success: "bg-success/10",
  warning: "bg-warning/10",
  danger: "bg-danger/10",
  info: "bg-info/10",
  primary: "bg-primary/10",
};

export function IconWrapper({
  children,
  variant = "default",
  size = "md",
  className,
}: IconWrapperProps) {
  const sizes = sizeMap[size];
  const bgColor = wrapperBgMap[variant];

  return (
    <div
      className={cn(
        "flex items-center justify-center rounded-xl",
        sizes.container,
        bgColor,
        className
      )}
    >
      {children}
    </div>
  );
}

/**
 * Notification badge with count.
 */
interface NotificationBadgeProps {
  count: number;
  max?: number;
  className?: string;
}

export function NotificationBadge({
  count,
  max = 99,
  className,
}: NotificationBadgeProps) {
  if (count <= 0) return null;

  const displayCount = count > max ? `${max}+` : count;

  return (
    <motion.span
      initial={{ scale: 0 }}
      animate={{ scale: 1 }}
      className={cn(
        "absolute -top-1 -right-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-danger px-1.5 text-[10px] font-bold text-white shadow-lg",
        className
      )}
    >
      {displayCount}
    </motion.span>
  );
}

/**
 * Sparkle effect for highlighting.
 */
export function SparkleEffect({ className }: { className?: string }) {
  return (
    <motion.div
      animate={{
        scale: [1, 1.2, 1],
        opacity: [0.7, 1, 0.7],
        rotate: [0, 180, 360],
      }}
      transition={{
        duration: 3,
        ease: "easeInOut",
        repeat: Infinity,
      }}
      className={cn("text-primary", className)}
    >
      <svg
        className="w-4 h-4"
        fill="currentColor"
        viewBox="0 0 24 24"
      >
        <path d="M12 0L14.59 9.41L24 12L14.59 14.59L12 24L9.41 14.59L0 12L9.41 9.41L12 0Z" />
      </svg>
    </motion.div>
  );
}
