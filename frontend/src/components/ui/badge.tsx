import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center justify-center rounded-full border px-2.5 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 [&>svg]:size-3 gap-1.5 [&>svg]:pointer-events-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive transition-all duration-200 overflow-hidden",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground [a&]:hover:bg-primary/90 shadow-sm",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "border-transparent bg-destructive text-white [a&]:hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60 shadow-sm",
        outline:
          "text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground border-border",
        // Semantic variants
        success:
          "border-transparent bg-success text-success-foreground shadow-sm [a&]:hover:bg-success/90",
        warning:
          "border-transparent bg-warning text-warning-foreground shadow-sm [a&]:hover:bg-warning/90",
        danger:
          "border-transparent bg-danger text-danger-foreground shadow-sm [a&]:hover:bg-danger/90",
        info:
          "border-transparent bg-info text-info-foreground shadow-sm [a&]:hover:bg-info/90",
        // Soft/subtle variants
        "success-soft":
          "border-success/20 bg-success/10 text-success dark:bg-success/20 dark:text-success",
        "warning-soft":
          "border-warning/20 bg-warning/10 text-warning dark:bg-warning/20 dark:text-warning",
        "danger-soft":
          "border-danger/20 bg-danger/10 text-danger dark:bg-danger/20 dark:text-danger",
        "info-soft":
          "border-info/20 bg-info/10 text-info dark:bg-info/20 dark:text-info",
        // Glow variants for emphasis
        "success-glow":
          "border-transparent bg-success text-success-foreground shadow-[0_0_10px_var(--glow-success)]",
        "danger-glow":
          "border-transparent bg-danger text-danger-foreground shadow-[0_0_10px_var(--glow-danger)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "span"

  return (
    <Comp
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
