import { Card, CardContent, CardHeader, CardTitle } from "@memory-bridge/ui";
import { Bot, Plus } from "lucide-react";

export default function AgentsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Agents</h2>
          <p className="text-muted text-sm mt-1">Manage your AI agents</p>
        </div>
        <button className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-light transition-colors">
          <Plus size={16} />
          New Agent
        </button>
      </div>

      <Card variant="glass">
        <CardContent className="flex flex-col items-center justify-center py-16">
          <Bot size={48} className="text-muted mb-4" />
          <p className="text-muted text-sm">No agents yet. Create your first agent to get started.</p>
        </CardContent>
      </Card>
    </div>
  );
}
