import { clsx } from "clsx";
import { forwardRef, type ButtonHTMLAttributes } from "react";

const buttonVariants = {
  primary: "bg-accent text-white hover:bg-accent-light shadow-lg shadow-accent/20 hover:shadow-accent/30",
  secondary: "bg-surface text-foreground hover:bg-border border border-border",
  ghost: "text-muted hover:text-foreground hover:bg-surface",
  danger: "bg-red-600 text-white hover:bg-red-500",
  outline: "border border-accent/30 text-accent hover:bg-accent/10",
} as const;

const buttonSizes = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4",
  lg: "h-12 px-6 text-base",
  icon: "h-10 w-10",
} as const;

type Variant = keyof typeof buttonVariants;
type Size = keyof typeof buttonSizes;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", loading, disabled, children, ...props }, ref) => {
    return (
      <button
        className={clsx(
          "inline-flex items-center justify-center whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
          buttonVariants[variant],
          buttonSizes[size],
          className
        )}
        ref={ref}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? (
          <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
        ) : null}
        {children}
      </button>
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
