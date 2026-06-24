"use client";

import { useEffect, useRef, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { getLogTail } from "@/lib/api/client";

export default function LogsPage() {
  const [lines, setLines] = useState<string[]>([]);
  const [auto, setAuto] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  // 初始加载
  useEffect(() => {
    getLogTail(200).then((r) => setLines(r.lines));
  }, []);

  // SSE 流
  useEffect(() => {
    if (!auto) {
      esRef.current?.close();
      esRef.current = null;
      return;
    }
    const es = new EventSource("/api/logs/stream");
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const line = JSON.parse(ev.data) as string;
        setLines((prev) => [...prev.slice(-800), line]);
      } catch {
        /* ignore */
      }
    };
    return () => es.close();
  }, [auto]);

  // 自动滚动到底
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">实时日志</h1>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Switch id="auto" checked={auto} onCheckedChange={setAuto} />
            <Label htmlFor="auto" className="text-sm">自动刷新</Label>
          </div>
          <a href="/api/logs/tail?n=2000" download>
            <Button variant="outline" size="sm">
              <Download className="h-4 w-4" />下载
            </Button>
          </a>
        </div>
      </div>

      <div
        ref={containerRef}
        className="border rounded-md bg-zinc-950 text-zinc-100 p-3 font-mono text-xs h-[calc(100vh-12rem)] overflow-y-auto"
      >
        {lines.length === 0 ? (
          <div className="text-zinc-500">暂无日志</div>
        ) : (
          lines.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-all py-0.5">
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
