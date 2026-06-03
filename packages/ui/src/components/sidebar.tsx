"use client";

import type { ReactNode } from "react";

interface SidebarProps {
  children: ReactNode;
  collapsed?: boolean;
}

export function Sidebar({ children, collapsed }: SidebarProps) {
  return (
    <aside
      className={`flex flex-col border-r border-border bg-sidebar transition-all duration-200 ${
        collapsed ? "w-16" : "w-64"
      }`}
    >
      <div className="flex h-14 items-center border-b border-border px-4">
        <span className="text-lg font-bold bg-gradient-to-r from-accent to-blue-400 bg-clip-text text-transparent">
          {collapsed ? "MB" : "Memory Bridge"}
        </span>
      </div>
      <nav className="flex-1 space-y-1 p-3 overflow-y-auto">{children}</nav>
    </aside>
  );
}

interface SidebarItemProps {
  icon: ReactNode;
  label: string;
  active?: boolean;
  collapsed?: boolean;
  onClick?: () => void;
}

export function SidebarItem({ icon, label, active, collapsed, onClick }: SidebarItemProps) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
        active
          ? "bg-accent/10 text-accent"
          : "text-muted hover:bg-surface hover:text-foreground"
      }`}
    >
      <span className="shrink-0">{icon}</span>
      {!collapsed && <span>{label}</span>}
    </button>
  );
}
