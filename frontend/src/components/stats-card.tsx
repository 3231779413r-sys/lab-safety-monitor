"use client";

import { motion } from "framer-motion";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AnimatedCounter } from "@/components/ui/animated-counter";
import { cn } from "@/lib/utils";

type CardVariant = "default" | "success" | "warning" | "danger" | "info";

interface StatsCardProps {
  title: string;
  value: string | number;
  description?: string;
  icon?: React.ReactNode;
  trend?: {
    value: number;
    isPositive: boolean;
  };
  loading?: boolean;
  variant?: CardVariant;
  animate?: boolean;
}

const variantStyles: Record<
  CardVariant,
  { iconBg: string; iconColor: string; accentBg: string; glowColor: string; borderColor: string; }
> = {
  default: {
    iconBg: "bg-primary/15 border-2 border-primary/40",
    iconColor: "text-primary",
    accentBg: "from-primary/10 via-primary/5 to-transparent",
    glowColor: "group-hover:glow-primary",
    borderColor: "border-primary/30",
  },
  success: {
    iconBg: "bg-success/15 border-2 border-success/40",
    iconColor: "text-success",
    accentBg: "from-success/10 via-success/5 to-transparent",
    glowColor: "group-hover:glow-success",
    borderColor: "border-success/30",
  },
  warning: {
    iconBg: "bg-warning/15 border-2 border-warning/40",
    iconColor: "text-warning",
    accentBg: "from-warning/10 via-warning/5 to-transparent",
    glowColor: "group-hover:glow-warning",
    borderColor: "border-warning/30",
  },
  danger: {
    iconBg: "bg-danger/15 border-2 border-danger/40",
    iconColor: "text-danger",
    accentBg: "from-danger/10 via-danger/5 to-transparent",
    glowColor: "group-hover:glow-danger",
    borderColor: "border-danger/30",
  },
  info: {
    iconBg: "bg-info/15 border-2 border-info/40",
    iconColor: "text-info",
    accentBg: "from-info/10 via-info/5 to-transparent",
    glowColor: "group-hover:glow-info",
    borderColor: "border-info/30",
  },
};

export function StatsCard({
  title,
  value,
  description,
  icon,
  trend,
  loading = false,
  variant = "default",
  animate = true,
}: StatsCardProps) {
  const styles = variantStyles[variant];

  if (loading) {
    return (
      <Card variant="glass" className="relative overflow-hidden corner-cut border-2">
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-1">
          <Skeleton className="h-3 w-16 shimmer corner-cut" />
          <Skeleton className="h-8 w-8 corner-cut shimmer" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-6 w-14 mb-1 shimmer corner-cut" />
          <Skeleton className="h-2 w-20 shimmer corner-cut" />
        </CardContent>
      </Card>
    );
  }

  // Parse numeric value for animation
  const numericValue =
    typeof value === "number"
      ? value
      : parseFloat(value.toString().replace(/[^0-9.-]/g, ""));
  const isPercentage =
    typeof value === "string" && value.toString().includes("%");
  const suffix = isPercentage ? "%" : "";
  const canAnimate = animate && !isNaN(numericValue);

  return (
    <motion.div
      whileHover={{ y: -2, scale: 1.01 }}
      transition={{ duration: 0.15, type: "spring", stiffness: 400, damping: 25 }}
    >
      <Card
        variant="glass"
        className={cn(
          "relative overflow-hidden group transition-all duration-200 corner-cut border",
          "hover:shadow-lg",
          styles.borderColor,
          styles.glowColor
        )}
      >
        {/* Diagonal stripe pattern on hover */}
        <div className="absolute inset-0 diagonal-stripes opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none" />

        {/* Animated gradient background */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className={cn(
            "absolute inset-0 bg-gradient-to-br opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none",
            styles.accentBg
          )}
        />

        {/* Tech border corners */}
        <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-primary opacity-50" />
        <div className="absolute top-0 right-0 w-2 h-2 border-t border-r border-primary opacity-50" />
        <div className="absolute bottom-0 left-0 w-2 h-2 border-b border-l border-primary opacity-50" />
        <div className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-primary opacity-50" />

        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-1 pt-3 px-3 relative">
          <CardTitle className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider font-mono">
            {title}
          </CardTitle>
          {icon && (
            <motion.div
              whileHover={{ scale: 1.1 }}
              whileTap={{ scale: 0.9 }}
              transition={{ duration: 0.2 }}
              className={cn(
                "flex items-center justify-center w-8 h-8 corner-cut transition-all duration-200 shadow-md",
                styles.iconBg
              )}
            >
              <div className={cn("w-4 h-4", styles.iconColor)}>{icon}</div>
            </motion.div>
          )}
        </CardHeader>
        <CardContent className="px-3 pb-3 relative">
          <motion.div
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.05 }}
            className="text-2xl font-black tracking-tight data-mono"
          >
            {canAnimate ? (
              <AnimatedCounter
                value={numericValue}
                suffix={suffix}
                decimals={isPercentage ? 1 : 0}
                duration={1000}
              />
            ) : (
              value
            )}
          </motion.div>
          {description && (
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, delay: 0.1 }}
              className="text-[10px] text-muted-foreground mt-1 font-medium tracking-wide"
            >
              {description}
            </motion.p>
          )}
          {trend && (
            <motion.div
              initial={{ opacity: 0, x: -5 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3, delay: 0.15 }}
              className={cn(
                "inline-flex items-center gap-1 text-[10px] font-bold mt-2 px-2 py-0.5 corner-cut border uppercase tracking-wider",
                trend.isPositive
                  ? "text-success bg-success/10 border-success/40"
                  : "text-danger bg-danger/10 border-danger/40"
              )}
            >
              <motion.span
                animate={{
                  y: trend.isPositive ? [0, -2, 0] : [0, 2, 0],
                  scale: [1, 1.1, 1]
                }}
                transition={{ duration: 1.5, repeat: Infinity }}
                className="text-xs"
              >
                {trend.isPositive ? "▲" : "▼"}
              </motion.span>
              <span>{Math.abs(trend.value)}%</span>
            </motion.div>
          )}

          {/* Status bar at bottom */}
          <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-transparent via-primary to-transparent opacity-30" />
        </CardContent>
      </Card>
    </motion.div>
  );
}
