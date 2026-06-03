import { Users, MemoryStick, Activity } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@memory-bridge/ui";

export default function OverviewPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">Overview</h2>
        <p className="text-muted text-sm mt-1">Your memory bridge at a glance</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted">Active Agents</CardTitle>
            <Users size={16} className="text-accent" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">0</div>
            <p className="text-xs text-muted mt-1">0 active in last 24h</p>
          </CardContent>
        </Card>
        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted">Memories Stored</CardTitle>
            <MemoryStick size={16} className="text-blue-400" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">0</div>
            <p className="text-xs text-muted mt-1">+0 today</p>
          </CardContent>
        </Card>
        <Card variant="glass">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted">API Calls</CardTitle>
            <Activity size={16} className="text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold">0</div>
            <p className="text-xs text-muted mt-1">In the last 7 days</p>
          </CardContent>
        </Card>
      </div>

      <Card variant="elevated">
        <CardHeader>
          <CardTitle>Getting Started</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted space-y-2">
          <p>1. Create an agent to start storing memories</p>
          <p>2. Use the API to read and write context</p>
          <p>3. Watch your memory graph grow</p>
        </CardContent>
      </Card>
    </div>
  );
}
