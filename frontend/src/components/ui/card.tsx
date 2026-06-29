import * as React from "react"

import { cn } from "@/lib/utils"

interface CardProps extends React.ComponentProps<"div"> {
  variant?: "default" | "glass" | "gradient" | "elevated" | "glow" | "success" | "danger" | "warning";
  hover?: boolean;
  glow?: "primary" | "success" | "warning" | "danger" | null;
}

function Card({ className, variant = "default", hover = false, glow = null, ...props }: CardProps) {
  return (
    <div
      data-slot="card"
      className={cn(
        "flex flex-col gap-6 rounded-xl border py-6 transition-all duration-300",
        // Default variant
        variant === "default" && "bg-card text-card-foreground shadow-sm",
        // Glassmorphism variant
        variant === "glass" && "glass text-card-foreground",
        // Gradient border variant
        variant === "gradient" && "bg-card text-card-foreground shadow-sm gradient-border",
        // Elevated variant with stronger shadow
        variant === "elevated" && "bg-card text-card-foreground shadow-lg",
        // Glow variant - animated glow effect
        variant === "glow" && "bg-card text-card-foreground shadow-lg pulse-glow border-primary/30",
        // Semantic variants
        variant === "success" && "bg-success/5 text-card-foreground border-success/20 shadow-sm",
        variant === "danger" && "bg-danger/5 text-card-foreground border-danger/20 shadow-sm",
        variant === "warning" && "bg-warning/5 text-card-foreground border-warning/20 shadow-sm",
        // Hover lift effect
        hover && "hover-lift cursor-pointer",
        // Glow effect on hover
        glow === "primary" && "hover:glow-primary hover:border-primary/40",
        glow === "success" && "hover:glow-success hover:border-success/40",
        glow === "warning" && "hover:glow-warning hover:border-warning/40",
        glow === "danger" && "hover:glow-danger hover:border-danger/40",
        className
      )}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "@container/card-header grid auto-rows-min grid-rows-[auto_auto] items-start gap-2 px-6 has-data-[slot=card-action]:grid-cols-[1fr_auto] [.border-b]:pb-4",
        className
      )}
      {...props}
    />
  )
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn("leading-none font-semibold", className)}
      {...props}
    />
  )
}

function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("text-muted-foreground text-sm", className)}
      {...props}
    />
  )
}

function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className
      )}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("px-6", className)}
      {...props}
    />
  )
}

function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn("flex items-center px-6 [.border-t]:pt-6", className)}
      {...props}
    />
  )
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
}
