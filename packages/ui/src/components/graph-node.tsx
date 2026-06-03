"use client";

import { type HTMLAttributes, type ReactNode } from "react";

interface GraphNodeProps extends HTMLAttributes<HTMLDivElement> {
  label: string;
  type?: "agent" | "memory" | "workspace" | "event";
  icon?: ReactNode;
  active?: boolean;
  connections?: number;
}

export function GraphNode({
  label,
  type = "memory",
  icon,
  active,
  connections,
  className,
  ...props
}: GraphNodeProps) {
  const typeColors: Record<string, string> = {
    agent: "border-blue-500 bg-blue-500/10",
    memory: "border-accent bg-accent/10",
    workspace: "border-emerald-500 bg-emerald-500/10",
    event: "border-amber-500 bg-amber-500/10",
  };

  const typeGradients: Record<string, string> = {
    agent: "from-blue-500 to-blue-600",
    memory: "from-accent to-purple-500",
    workspace: "from-emerald-500 to-teal-500",
    event: "from-amber-500 to-orange-500",
  };

  return (
    <div
      className={`relative flex flex-col items-center gap-2 rounded-xl border-2 p-3 min-w-[120px] transition-all duration-200 ${
        typeColors[type]
      } ${
        active ? "shadow-lg shadow-glow scale-105" : ""
      } ${className || ""}`}
      {...props}
    >
      {icon && (
        <div
          className={`w-10 h-10 rounded-full flex items-center justify-center bg-gradient-to-br ${typeGradients[type]}`}
        >
          {icon}
        </div>
      )}
      <span className="text-xs font-medium text-center">{label}</span>
      {connections !== undefined && (
        <span className="text-[10px] text-muted">{connections} connections</span>
      )}
    </div>
  );
}
