import type { ReactNode } from "react";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Top Nav */}
      <header className="sticky top-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
          <div className="flex items-center gap-8">
            <span className="text-xl font-bold bg-gradient-to-r from-accent to-blue-400 bg-clip-text text-transparent">
              Memory Bridge
            </span>
            <nav className="hidden md:flex items-center gap-6">
              <a href="#features" className="text-sm text-muted hover:text-foreground transition-colors">Features</a>
              <a href="#docs" className="text-sm text-muted hover:text-foreground transition-colors">Docs</a>
              <a href="#pricing" className="text-sm text-muted hover:text-foreground transition-colors">Pricing</a>
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <a href="/login" className="text-sm text-muted hover:text-foreground transition-colors">Sign In</a>
            <a
              href="/signup"
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-light transition-colors"
            >
              Get Started
            </a>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1">{children}</main>

      {/* Footer */}
      <footer className="border-t border-border bg-surface py-12">
        <div className="mx-auto max-w-7xl px-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
            <div>
              <h4 className="text-sm font-semibold mb-3">Product</h4>
              <ul className="space-y-2 text-sm text-muted">
                <li><a href="#" className="hover:text-foreground transition-colors">Features</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Pricing</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Docs</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Changelog</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold mb-3">Integrations</h4>
              <ul className="space-y-2 text-sm text-muted">
                <li><a href="#" className="hover:text-foreground transition-colors">Claude</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">OpenAI</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">LangChain</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Custom SDK</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold mb-3">Company</h4>
              <ul className="space-y-2 text-sm text-muted">
                <li><a href="#" className="hover:text-foreground transition-colors">Blog</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Careers</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Contact</a></li>
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-semibold mb-3">Legal</h4>
              <ul className="space-y-2 text-sm text-muted">
                <li><a href="#" className="hover:text-foreground transition-colors">Privacy</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Terms</a></li>
                <li><a href="#" className="hover:text-foreground transition-colors">Security</a></li>
              </ul>
            </div>
          </div>
          <div className="mt-12 border-t border-border pt-6 text-center text-sm text-muted">
            © 2026 Memory Bridge. All rights reserved.
          </div>
        </div>
      </footer>
    </div>
  );
}
