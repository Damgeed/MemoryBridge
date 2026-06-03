export function CodeBlock({ language = "typescript", code }: { language?: string; code: string }) {
  return (
    <div className="group relative rounded-xl overflow-hidden">
      {language && (
        <div className="absolute top-0 right-0 px-3 py-1 text-xs text-muted bg-surface/50 rounded-bl-lg border-l border-b border-border">
          {language}
        </div>
      )}
      <pre className="overflow-x-auto bg-[#0d0d14] p-4 text-sm leading-relaxed">
        <code className="text-foreground/90 font-mono">{code}</code>
      </pre>
    </div>
  );
}
