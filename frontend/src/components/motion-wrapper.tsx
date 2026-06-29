"use client";

import { motion, AnimatePresence, Variants } from "framer-motion";
import { ReactNode } from "react";

// 页面动画变体
const pageVariants: Variants = {
  initial: { opacity: 0, y: 20 },
  enter: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94], when: "beforeChildren", staggerChildren: 0.1 } },
  exit: { opacity: 0, y: -10, transition: { duration: 0.2, ease: [0.25, 0.46, 0.45, 0.94] } },
};

const fadeVariants: Variants = {
  initial: { opacity: 0 },
  enter: { opacity: 1, transition: { duration: 0.3, ease: "easeOut" } },
  exit: { opacity: 0, transition: { duration: 0.2, ease: "easeIn" } },
};

const slideUpVariants: Variants = {
  initial: { opacity: 0, y: 30 },
  enter: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.25, 0.46, 0.45, 0.94] } },
  exit: { opacity: 0, y: -20, transition: { duration: 0.3, ease: "easeIn" } },
};

const scaleVariants: Variants = {
  initial: { opacity: 0, scale: 0.95 },
  enter: { opacity: 1, scale: 1, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] } },
  exit: { opacity: 0, scale: 0.98, transition: { duration: 0.2, ease: "easeIn" } },
};

const variantMap: Record<string, Variants> = {
  page: pageVariants,
  fade: fadeVariants,
  slideUp: slideUpVariants,
  scale: scaleVariants,
};

interface MotionWrapperProps {
  children: ReactNode;
  className?: string;
  type?: string;
  delay?: number;
}

// 页面过渡包装器
export function MotionWrapper({ children, className, type = "page", delay = 0 }: MotionWrapperProps) {
  return (
    <motion.div
      initial="initial"
      animate="enter"
      exit="exit"
      variants={variantMap[type]}
      className={className}
      style={{ willChange: "opacity, transform" }}
      transition={{ delay }}
    >
      {children}
    </motion.div>
  );
}

export const staggerChildVariants: Variants = {
  initial: { opacity: 0, y: 20 },
  enter: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] } },
};

interface StaggerContainerProps {
  children: ReactNode;
  className?: string;
  staggerDelay?: number;
  initialDelay?: number;
}

export function StaggerContainer({ children, className, staggerDelay = 0.08, initialDelay = 0.1 }: StaggerContainerProps) {
  return (
    <motion.div
      initial="initial"
      animate="enter"
      variants={{ initial: {}, enter: { transition: { staggerChildren: staggerDelay, delayChildren: initialDelay } } }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

interface StaggerItemProps {
  children: ReactNode;
  className?: string;
  index?: number;
}

export function StaggerItem({ children, className }: StaggerItemProps) {
  return (
    <motion.div variants={staggerChildVariants} className={className} style={{ willChange: "opacity, transform" }}>
      {children}
    </motion.div>
  );
}

interface HoverScaleProps {
  children: ReactNode;
  className?: string;
  scale?: number;
}

export function HoverScale({ children, className, scale = 1.02 }: HoverScaleProps) {
  return (
    <motion.div whileHover={{ scale }} whileTap={{ scale: 0.98 }} transition={{ duration: 0.2 }} className={className}>
      {children}
    </motion.div>
  );
}

interface AnimatePresenceWrapperProps {
  children: ReactNode;
  show: boolean;
  className?: string;
}

export function AnimatePresenceWrapper({ children, show, className }: AnimatePresenceWrapperProps) {
  return (
    <AnimatePresence mode="wait">
      {show && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.3 }}
          className={className}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function FloatingElement({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div animate={{ y: [0, -5, 0] }} transition={{ duration: 3, ease: "easeInOut", repeat: Infinity }} className={className}>
      {children}
    </motion.div>
  );
}

export function PulseElement({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div animate={{ scale: [1, 1.05, 1], opacity: [1, 0.8, 1] }} transition={{ duration: 2, ease: "easeInOut", repeat: Infinity }} className={className}>
      {children}
    </motion.div>
  );
}

export function CountUp({ value, className }: { value: number; className?: string }) {
  return (
    <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className={className}>
      {value}
    </motion.span>
  );
}
