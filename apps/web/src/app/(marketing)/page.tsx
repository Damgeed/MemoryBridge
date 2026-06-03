import { AnimatedGradient, Button, Card, CardContent, CardHeader, CardTitle, CodeBlock } from "@memory-bridge/ui";
import { ArrowRight, Bot, Brain, Network, Shield, Zap, Layers } from "lucide-react";

const features = [
  { icon: <Brain size={24} />, title: "Persistent Memory", description: "Agents remember context across sessions, conversations, and API calls." },
  { icon: <Network size={24} />, title: "Memory Graph", description: "Visualize how memories connect — agents, topics, and relationships in one view." },
  { icon: <Zap size={24} />, title: "Real-time Sync", description: "Memories update in real-time across all connected agents and services." },
  { icon: <Shield size={24} />, title: "Enterprise Security", description: "JWT authentication, workspace isolation, and per-agent access control." },
  { icon: <Layers size={24} />, title: "Multi-framework", description: "Works with Claude, OpenAI, LangChain, and any custom agent framework." },
  { icon: <Bot size={24} />, title: "Semantic Recall", description: "Natural language search across all stored memories with semantic ranking." },
];

const frameworks = ["Claude", "OpenAI", "LangChain", "LlamaIndex", "CrewAI", "AutoGen"];

export default function HomePage() {
  return (
    <div className="relative">
      <AnimatedGradient />

      {/* Hero */}
      <section className="mx-auto max-w-7xl px-6 py-24 text-center">
        <div className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/5 px-4 py-1.5 text-sm text-accent mb-8">
          <span className="h-2 w-2 rounded-full bg-accent animate-pulse" />
          Now in Beta — Free tier available
        </div>
        <h1 className="text-5xl md:text-7xl font-bold tracking-tight mb-6">
          Memory that{" "}
          <span className="bg-gradient-to-r from-accent to-blue-400 bg-clip-text text-transparent">
            bridges
          </span>{" "}
          your agents
        </h1>
        <p className="text-lg text-muted max-w-2xl mx-auto mb-10">
          Persistent, searchable memory for multi-agent AI teams. Store context once,
          recall anywhere — across sessions, frameworks, and deployments.
        </p>
        <div className="flex items-center justify-center gap-4">
          <Button size="lg">
            Get Started Free <ArrowRight size={16} className="ml-2" />
          </Button>
          <Button variant="outline" size="lg">
            View Docs
          </Button>
        </div>
      </section>

      {/* Framework Logos */}
      <section className="mx-auto max-w-7xl px-6 py-12">
        <p className="text-center text-xs text-muted uppercase tracking-widest mb-8">Works with any framework</p>
        <div className="flex flex-wrap items-center justify-center gap-8 opacity-40">
          {frameworks.map((f) => (
            <span key={f} className="text-lg font-semibold text-foreground/60">{f}</span>
          ))}
        </div>
      </section>

      {/* Feature Cards */}
      <section id="features" className="mx-auto max-w-7xl px-6 py-24">
        <h2 className="text-3xl md:text-4xl font-bold text-center mb-4">Everything your agents need to remember</h2>
        <p className="text-muted text-center mb-16 max-w-xl mx-auto">
          From simple key-value storage to semantic graph search, Memory Bridge scales with your team.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f) => (
            <Card key={f.title} variant="glass" className="hover:border-accent/30 transition-colors">
              <CardHeader>
                <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center text-accent mb-2">
                  {f.icon}
                </div>
                <CardTitle>{f.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted">{f.description}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* API Preview */}
      <section className="mx-auto max-w-4xl px-6 py-24">
        <h2 className="text-3xl md:text-4xl font-bold text-center mb-4">Simple API, powerful memory</h2>
        <p className="text-muted text-center mb-12 max-w-xl mx-auto">
          One API call to store. One to recall. Works with any language or framework.
        </p>
        <Card variant="elevated" className="overflow-hidden">
          <div className="flex border-b border-border">
            <button className="px-4 py-2 text-sm border-b-2 border-accent text-accent">JavaScript</button>
            <button className="px-4 py-2 text-sm text-muted">Python</button>
            <button className="px-4 py-2 text-sm text-muted">cURL</button>
          </div>
          <CodeBlock
            language="typescript"
            code={`import { MemoryBridge } from '@memory-bridge/sdk';\n\nconst mb = new MemoryBridge({ apiKey: 'mb_...' });\n\n// Store memory\nawait mb.memories.set('user_preference', {\n  theme: 'dark',\n  language: 'en'\n});\n\n// Semantic recall\nconst results = await mb.memories.search('user preferences');\nconsole.log(results);`}
          />
        </Card>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-4xl px-6 py-24 text-center">
        <Card variant="gradient" className="p-12">
          <h2 className="text-3xl md:text-4xl font-bold mb-4">Ready to bridge your agents?</h2>
          <p className="text-muted mb-8 max-w-lg mx-auto">
            Start building with Memory Bridge today. Free tier includes 1,000 memories and 3 agents.
          </p>
          <Button size="lg">
            Start Free <ArrowRight size={16} className="ml-2" />
          </Button>
        </Card>
      </section>
    </div>
  );
}
