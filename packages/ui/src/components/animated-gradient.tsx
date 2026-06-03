"use client";

import { type HTMLAttributes } from "react";

interface AnimatedGradientProps extends HTMLAttributes<HTMLDivElement> {
  colors?: string[];
  speed?: number;
}

export function AnimatedGradient({
  colors = ["#7c3aed", "#3b82f6", "#06b6d4", "#8b5cf6"],
  speed = 8,
  className,
  ...props
}: AnimatedGradientProps) {
  return (
    <div
      className={`absolute inset-0 -z-10 overflow-hidden ${className || ""}`}
      aria-hidden="true"
      {...props}
    >
      <div
        className="absolute inset-0 opacity-20 blur-3xl"
        style={{
          background: `linear-gradient(135deg, ${colors.join(", ")})`,
          backgroundSize: "400% 400%",
          animation: `gradient-shift ${speed}s ease infinite`,
        }}
      />
    </div>
  );
}
