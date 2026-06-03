"use client";

import { useState, type ReactNode } from "react";
import { Sidebar, SidebarItem, Topbar } from "@memory-bridge/ui";
import {
  LayoutDashboard,
  Bot,
  Brain,
  Network,
  Play,
  Settings,
  Menu,
  X,
} from "lucide-react";

const navItems = [
  { icon: <LayoutDashboard size={18} />, label: "Overview", href: "/overview" },
  { icon: <Bot size={18} />, label: "Agents", href: "/agents" },
  { icon: <Brain size={18} />, label: "Memories", href: "/memories" },
  { icon: <Network size={18} />, label: "Graph", href: "/graph" },
  { icon: <Play size={18} />, label: "Playground", href: "/playground" },
];

export default function DashboardLayout({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={`${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        } fixed inset-y-0 left-0 z-50 md:relative md:translate-x-0 transition-transform duration-200`}
      >
        <Sidebar collapsed={collapsed}>
          <div className="md:hidden absolute top-3 right-3">
            <button onClick={() => setMobileOpen(false)} className="text-muted hover:text-foreground">
              <X size={18} />
            </button>
          </div>
          {navItems.map((item) => (
            <SidebarItem key={item.href} {...item} collapsed={collapsed} />
          ))}
        </Sidebar>
      </div>

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar
          title=""
          actions={
            <>
              <button
                onClick={() => setCollapsed(!collapsed)}
                className="hidden md:flex items-center justify-center h-8 w-8 rounded-lg text-muted hover:text-foreground hover:bg-surface transition-colors"
              >
                <Menu size={16} />
              </button>
              <button
                onClick={() => setMobileOpen(true)}
                className="md:hidden flex items-center justify-center h-8 w-8 rounded-lg text-muted hover:text-foreground hover:bg-surface transition-colors"
              >
                <Menu size={16} />
              </button>
              <button className="flex items-center justify-center h-8 w-8 rounded-lg text-muted hover:text-foreground hover:bg-surface transition-colors">
                <Settings size={16} />
              </button>
            </>
          }
        />
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
