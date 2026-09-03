"use client";

import { FileText, Presentation, Video, RefreshCw, ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { getArtifact, type Artifact } from "@/lib/api";

export function ArtifactCard({ id, onOpen }: { id: string; onOpen: (artifact: Artifact) => void }) {
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await getArtifact(id, controller.signal);
        if (controller.signal.aborted) return;
        setArtifact(next);
        setError("");
        if (next.state === "queued" || next.state === "running") timer = setTimeout(poll, 3000);
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the artifact.");
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, attempt]);
  const Icon = artifact?.format === "video" ? Video : artifact?.format === "pptx" ? Presentation : FileText;
  const pending = !error && (!artifact || artifact.state === "queued" || artifact.state === "running");
  return (
    <div className="mt-4 flex max-w-lg items-start gap-3 rounded-xl border bg-background p-4">
      <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-medium break-words">{artifact?.title || "Preparing your file"}</p>
        <p className="mt-1 text-xs text-muted-foreground" role="status">
          {error || artifact?.error || (pending ? (artifact?.state === "queued" ? "Queued · You can keep chatting" : "Generating · You can keep chatting") : `${artifact?.format.toUpperCase()}${artifact?.slides ? ` · ${artifact.slides} slides` : ""} · Ready`)}
        </p>
        {error && <button onClick={() => setAttempt(n => n + 1)} className="mt-3 flex items-center gap-1.5 text-xs underline underline-offset-4"><RefreshCw className="size-3" />Retry connection</button>}
        {artifact?.state === "failed" && <p className="mt-2 text-xs text-muted-foreground">Ask again in chat to start a new generation.</p>}
        {artifact?.state === "done" && !error && <button onClick={() => onOpen(artifact)} className="mt-3 inline-flex min-h-9 items-center gap-2 rounded-md bg-foreground px-3 text-xs font-medium text-background focus-visible:outline-2 focus-visible:outline-offset-2">Open {artifact.format === "video" ? "video" : artifact.format === "pdf" ? "PDF" : "presentation"}<ArrowUpRight className="size-3.5" /></button>}
      </div>
    </div>
  );
}
