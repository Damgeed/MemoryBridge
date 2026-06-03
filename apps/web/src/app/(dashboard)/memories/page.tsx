import { Card, CardContent } from "@memory-bridge/ui";
import { Brain, Search } from "lucide-react";

export default function MemoriesPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Memories</h2>
          <p className="text-muted text-sm mt-1">Browse and search stored memories</p>
        </div>
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            placeholder="Search memories..."
            className="h-10 w-64 rounded-lg border border-border bg-surface pl-10 pr-4 text-sm text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>
      </div>

      <Card variant="glass">
        <CardContent className="flex flex-col items-center justify-center py-16">
          <Brain size={48} className="text-muted mb-4" />
          <p className="text-muted text-sm">No memories stored yet. Memories will appear here once agents start storing context.</p>
        </CardContent>
      </Card>
    </div>
  );
}
