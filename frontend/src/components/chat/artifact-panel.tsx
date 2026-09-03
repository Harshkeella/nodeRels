"use client";

import { Download, ExternalLink, Maximize2, Minimize2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { artifactAccess, type Artifact } from "@/lib/api";

export function ArtifactPanel({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  const [access, setAccess] = useState<Awaited<ReturnType<typeof artifactAccess>> | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [mobile, setMobile] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const closeCallback = useRef(onClose);
  useEffect(() => { closeCallback.current = onClose; }, [onClose]);
  useEffect(() => {
    const focused = document.activeElement as HTMLElement;
    close.current?.focus();
    const query = window.matchMedia("(max-width: 1023px)");
    const resize = () => setMobile(query.matches);
    resize(); query.addEventListener("change", resize);
    let cancelled = false;
    artifactAccess(artifact.id).then(a => { if (!cancelled) setAccess(a); }).catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; query.removeEventListener("change", resize); focused?.focus(); };
  }, [artifact.id]);
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") { if (expanded) setExpanded(false); else closeCallback.current(); }
      if (event.key === "Tab" && (expanded || mobile)) {
        const nodes = panel.current?.querySelectorAll<HTMLElement>("button, a[href], iframe, video");
        if (!nodes?.length) return;
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [expanded, mobile]);
  useEffect(() => {
    if (!(expanded || mobile)) return;
    const siblings = Array.from(panel.current?.parentElement?.children || []).filter(node => node !== panel.current) as HTMLElement[];
    const previous = siblings.map(node => node.inert);
    siblings.forEach(node => { node.inert = true; });
    return () => siblings.forEach((node, index) => { node.inert = previous[index]; });
  }, [expanded, mobile]);
  useEffect(() => {
    function message(event: MessageEvent) {
      if (!access || event.origin !== new URL(access.studio).origin || event.source !== panel.current?.querySelector('iframe')?.contentWindow || event.data?.type !== 'noderels-preview-escape') return;
      if (expanded) setExpanded(false); else closeCallback.current();
    }
    window.addEventListener('message', message);
    return () => window.removeEventListener('message', message);
  }, [access, expanded]);
  async function edit() {
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    try {
      const a = await artifactAccess(artifact.id, "edit");
      if (tab) tab.location.href = a.studio;
      else setError("Allow pop-ups to open Deck Studio, then try again.");
    } catch (e) { tab?.close(); setError(e instanceof Error ? e.message : "Could not open the editor."); }
  }
  const filename = artifact.format === "video" ? "video.mp4" : artifact.format === "pdf" ? "document.pdf" : "deck.pptx";
  return (
    <aside ref={panel} role="dialog" aria-modal={mobile || expanded} aria-labelledby="artifact-title"
      className={`${expanded ? "fixed inset-0 z-50" : "fixed inset-0 z-40 lg:static lg:z-auto lg:w-[52%] lg:min-w-[420px]"} flex min-w-0 flex-col border-l bg-background`}>
      <header className="flex items-center gap-2 border-b px-4 py-3">
        <div className="min-w-0 flex-1"><h2 id="artifact-title" className="truncate text-sm font-semibold" title={artifact.title}>{artifact.title}</h2><p className="mt-0.5 text-xs text-muted-foreground">{artifact.format.toUpperCase()}{artifact.slides ? ` · ${artifact.slides} slides` : ""}</p></div>
        <button onClick={() => setExpanded(v => !v)} aria-label={expanded ? "Exit fullscreen preview" : "Fullscreen preview"} className="rounded-md p-2 hover:bg-muted focus-visible:outline-2">{expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}</button>
        <button ref={close} onClick={onClose} aria-label="Close preview" className="rounded-md p-2 hover:bg-muted focus-visible:outline-2"><X className="size-4" /></button>
      </header>
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5 text-xs">
        {access && <a href={access.file(filename, true)} download className="inline-flex min-h-9 items-center gap-1.5 rounded-md border px-3 hover:bg-muted"><Download className="size-3.5" />Download {artifact.format === "video" ? "video" : artifact.format === "pdf" ? "PDF" : "PPTX"}</a>}
        {artifact.format === "video" && access && <a href={access.file("deck.pptx", true)} download className="inline-flex min-h-9 items-center gap-1.5 rounded-md border px-3 hover:bg-muted"><Download className="size-3.5" />Download PPTX</a>}
        {artifact.format !== "pdf" && <button onClick={edit} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 hover:bg-muted"><ExternalLink className="size-3.5" />Edit in Deck Studio</button>}
      </div>
      {error && <p role="alert" className="p-4 text-sm text-destructive">{error} Close and reopen this preview to retry.</p>}
      <div className="flex min-h-0 flex-1 items-center justify-center bg-muted/40">
        {!access ? <p role="status" className="text-sm text-muted-foreground">Opening preview…</p> : artifact.format === "video" ?
          <video className="max-h-full w-full" controls playsInline preload="metadata" src={access.file("video.mp4")} aria-label={artifact.title} onError={() => setError("The video could not be loaded.")} /> :
          <iframe title={`${artifact.title} preview`} className="h-full w-full border-0" src={artifact.format === "pdf" ? access.file("document.pdf") : access.studio} allow="fullscreen" referrerPolicy="no-referrer" />}
      </div>
    </aside>
  );
}
