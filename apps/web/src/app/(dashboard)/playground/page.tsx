import { Card, CardContent, CardHeader, CardTitle } from "@memory-bridge/ui";
import { Play, Terminal, Code } from "lucide-react";

export default function PlaygroundPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">Playground</h2>
        <p className="text-muted text-sm mt-1">Experiment with Memory Bridge APIs</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card variant="glass">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Terminal size={16} className="text-accent" />
              <CardTitle>Quick Test</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <pre className="rounded-lg bg-[#0d0d14] p-4 text-sm font-mono text-foreground/90 overflow-x-auto">
              <code>{`// Store a memory\nawait fetch('/api/v1/memories', {\n  method: 'POST',\n  headers: { 'Content-Type': 'application/json' },\n  body: JSON.stringify({\n    key: 'user_preference',\n    value: { theme: 'dark' }\n  })\n})`}</code>
            </pre>
          </CardContent>
        </Card>
        <Card variant="glass">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Code size={16} className="text-blue-400" />
              <CardTitle>SDK Examples</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <pre className="rounded-lg bg-[#0d0d14] p-4 text-sm font-mono text-foreground/90 overflow-x-auto">
              <code>{`import { MemoryBridge } from '@memory-bridge/sdk';\n\nconst mb = new MemoryBridge({\n  apiKey: process.env.MB_API_KEY\n});\n\nawait mb.memories.set('key', { data: 'hello' });\nconst memory = await mb.memories.get('key');`}</code>
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
