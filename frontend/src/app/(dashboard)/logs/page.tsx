"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { getLogTail } from "@/lib/api/client";

export default function LogsPage() {
  const { t } = useTranslation("logs");
  const [lines, setLines] = useState<string[]>([]);
  const [auto, setAuto] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => { void getLogTail(200).then((result) => setLines(result.lines)); }, []);
  useEffect(() => {
    if (!auto) return;
    const stream = new EventSource("/api/logs/stream");
    stream.onmessage = (event) => { try { setLines((previous) => [...previous.slice(-800), JSON.parse(event.data) as string]); } catch { /* ignore malformed log events */ } };
    return () => stream.close();
  }, [auto]);
  useEffect(() => { if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight; }, [lines]);
  return <div className="space-y-4"><div className="flex items-center justify-between"><h1 className="text-xl font-bold">{t("title")}</h1><div className="flex items-center gap-4"><div className="flex items-center gap-2"><Switch id="auto" checked={auto} onCheckedChange={setAuto} /><Label htmlFor="auto" className="text-sm">{t("autoRefresh")}</Label></div><a href="/api/logs/tail?n=2000" download><Button variant="outline" size="sm"><Download className="h-4 w-4" />{t("download")}</Button></a></div></div><div ref={containerRef} className="border rounded-md bg-zinc-950 text-zinc-100 p-3 font-mono text-xs h-[calc(100vh-12rem)] overflow-y-auto">{lines.length === 0 ? <div className="text-zinc-500">{t("empty")}</div> : lines.map((line, index) => <div key={index} className="whitespace-pre-wrap break-all py-0.5">{line}</div>)}</div></div>;
}
