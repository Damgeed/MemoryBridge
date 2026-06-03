"use client";

import { type HTMLAttributes, type ReactNode } from "react";

interface TopbarProps extends HTMLAttributes<HTMLElement> {
  title?: string;
  actions?: ReactNode;
}

export function Topbar({ title, actions, className, ...props }: TopbarProps) {
  return (
    <header
      className={`flex h-14 items-center justify-between border-b border-border bg-background/80 backdrop-blur-md px-6 ${className || ""}`}
      {...props}
    >
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">{title}</h1>
      </div>
      <div className="flex items-center gap-2">{actions}</div>
    </header>
  );
}
