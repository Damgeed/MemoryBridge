import { Card, CardContent } from "@memory-bridge/ui";
import { Network } from "lucide-react";

export default function GraphPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">Graph</h2>
        <p className="text-muted text-sm mt-1">Visualize your memory connections</p>
      </div>

      <Card variant="glass" className="min-h-[500px] flex items-center justify-center">
        <CardContent className="flex flex-col items-center">
          <Network size={48} className="text-muted mb-4" />
          <p className="text-muted text-sm">Graph visualization will appear here once memories are stored.</p>
        </CardContent>
      </Card>
    </div>
  );
}
