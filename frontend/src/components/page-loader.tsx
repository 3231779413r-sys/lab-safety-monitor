"use client";

import { motion } from "framer-motion";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Full-page loading skeleton with animations.
 */
export function PageLoader({ className }: { className?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className={cn("space-y-8", className)}
    >
      {/* Header skeleton */}
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <Skeleton className="h-8 w-48 shimmer" />
          <Skeleton className="h-4 w-64 shimmer" />
        </div>
        <Skeleton className="h-10 w-40 rounded-full shimmer" />
      </div>

      {/* Stats grid skeleton */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.1 }}
          >
            <div className="p-6 rounded-xl border border-border/50 bg-card">
              <div className="flex items-center justify-between mb-4">
                <Skeleton className="h-4 w-24 shimmer" />
                <Skeleton className="h-10 w-10 rounded-lg shimmer" />
              </div>
              <Skeleton className="h-8 w-20 mb-2 shimmer" />
              <Skeleton className="h-3 w-32 shimmer" />
            </div>
          </motion.div>
        ))}
      </div>

      {/* Chart skeleton */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {[...Array(2)].map((_, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 + i * 0.1 }}
          >
            <div className="p-6 rounded-xl border border-border/50 bg-card">
              <div className="flex items-center gap-3 mb-6">
                <Skeleton className="h-8 w-8 rounded-lg shimmer" />
                <Skeleton className="h-5 w-40 shimmer" />
              </div>
              <Skeleton className="h-[250px] w-full rounded-lg shimmer" />
            </div>
          </motion.div>
        ))}
      </div>

      {/* List skeleton */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.6 }}
      >
        <div className="p-6 rounded-xl border border-border/50 bg-card">
          <div className="flex items-center gap-3 mb-6">
            <Skeleton className="h-8 w-8 rounded-lg shimmer" />
            <Skeleton className="h-5 w-40 shimmer" />
          </div>
          <div className="space-y-4">
            {[...Array(3)].map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-4 p-4 rounded-xl border border-border/30"
              >
                <Skeleton className="h-10 w-10 rounded-xl shimmer" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-48 shimmer" />
                  <Skeleton className="h-3 w-32 shimmer" />
                </div>
                <Skeleton className="h-6 w-20 rounded-full shimmer" />
              </div>
            ))}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

/**
 * Card loading skeleton.
 */
export function CardLoader({ className }: { className?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={cn(
        "p-6 rounded-xl border border-border/50 bg-card",
        className
      )}
    >
      <div className="flex items-center justify-between mb-4">
        <Skeleton className="h-4 w-24 shimmer" />
        <Skeleton className="h-10 w-10 rounded-lg shimmer" />
      </div>
      <Skeleton className="h-8 w-20 mb-2 shimmer" />
      <Skeleton className="h-3 w-32 shimmer" />
    </motion.div>
  );
}

/**
 * Table loading skeleton.
 */
export function TableLoader({
  rows = 5,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-3", className)}>
      {[...Array(rows)].map((_, i) => (
        <motion.div
          key={i}
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: i * 0.05 }}
          className="flex items-center gap-4 p-4 rounded-xl border border-border/50"
        >
          <Skeleton className="h-10 w-10 rounded-xl shimmer" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-48 shimmer" />
            <Skeleton className="h-3 w-32 shimmer" />
          </div>
          <Skeleton className="h-6 w-20 rounded-full shimmer" />
        </motion.div>
      ))}
    </div>
  );
}

/**
 * Inline loading spinner.
 */
export function LoadingSpinner({
  size = "md",
  className,
}: {
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const sizeClasses = {
    sm: "h-4 w-4",
    md: "h-6 w-6",
    lg: "h-8 w-8",
  };

  return (
    <motion.div
      animate={{ rotate: 360 }}
      transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
      className={cn(sizeClasses[size], className)}
    >
      <svg
        className="text-primary"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle
          className="opacity-25"
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="4"
        />
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
        />
      </svg>
    </motion.div>
  );
}

/**
 * Centered loading state.
 */
export function CenteredLoader({ message = "Loading..." }: { message?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="flex flex-col items-center justify-center py-16 gap-4"
    >
      <LoadingSpinner size="lg" />
      <p className="text-sm text-muted-foreground">{message}</p>
    </motion.div>
  );
}
