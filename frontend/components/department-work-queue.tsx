"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import type { DSRWorkItem } from "@/lib/types";

type AssignedWork = DSRWorkItem & {
  request_id: number;
  request_type: string;
  case_stage: string;
};

const statuses = [
  ["OPEN", "Open"],
  ["IN_PROGRESS", "In progress"],
  ["COMPLETED", "Completed"],
  ["BLOCKED", "Blocked"],
] as const;

export function DepartmentWorkQueue({ principalId }: { principalId: string }) {
  const [items, setItems] = useState<AssignedWork[]>([]);
  const [summaries, setSummaries] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<number | null>(null);
  const [error, setError] = useState("");

  async function loadItems() {
    setError("");
    try {
      const response = await fetch("/api/assigned-work", { cache: "no-store" });
      const body = (await response.json()) as AssignedWork[] | { detail?: string };
      if (!response.ok || !Array.isArray(body)) {
        throw new Error(!Array.isArray(body) ? body.detail : "Could not load assigned work.");
      }
      setItems(body);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load assigned work.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function loadAssignedItems() {
      setError("");
      try {
        const response = await fetch("/api/assigned-work", { cache: "no-store" });
        const body = (await response.json()) as AssignedWork[] | { detail?: string };
        if (!response.ok || !Array.isArray(body)) {
          throw new Error(!Array.isArray(body) ? body.detail : "Could not load assigned work.");
        }
        if (!cancelled) setItems(body);
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Could not load assigned work.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadAssignedItems();
    return () => {
      cancelled = true;
    };
  }, []);

  async function updateItem(item: AssignedWork, status: string) {
    const completionSummary = summaries[item.id]?.trim() ?? item.completion_summary;
    if (status === "COMPLETED" && !completionSummary) {
      toast.error("Record a completion summary before completing this work item.");
      return;
    }
    setSaving(item.id);
    try {
      const response = await fetch(
        `/api/assigned-work/${item.request_id}/${item.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, completion_summary: completionSummary }),
        },
      );
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not update assigned work.");
      toast.success("Assigned work updated.");
      await loadItems();
    } catch (updateError) {
      toast.error(updateError instanceof Error ? updateError.message : "Could not update assigned work.");
    } finally {
      setSaving(null);
    }
  }

  return (
    <AppShell role="department_contributor" principalId={principalId}>
      <section className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          ASSIGNED DEPARTMENT WORK
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">My work items</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          You can update only work items assigned to this mock contributor identity.
          Record completion notes without adding clinical or source-record content.
        </p>
      </section>
      {error && <p role="alert" className="mb-4 text-sm text-destructive">{error}</p>}
      {loading ? (
        <p className="text-sm text-muted-foreground">Loading assigned work…</p>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No work items are assigned to this identity.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {items.map((item) => (
            <Card key={item.id}>
              <CardHeader>
                <CardTitle>{item.title}</CardTitle>
                <CardDescription>
                  DSR-{item.request_id} · {item.request_type} · {item.task_type.replaceAll("_", " ")}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-sm">{item.details || "No additional instructions."}</p>
                <p className="text-xs text-muted-foreground">
                  Stage: {item.case_stage.replaceAll("_", " ")}
                  {item.due_at ? ` · Due ${new Date(item.due_at).toLocaleString()}` : ""}
                </p>
                <Link className="text-sm font-medium text-primary underline" href={`/department`}>
                  Case reference DSR-{item.request_id}
                </Link>
                <Textarea
                  aria-label={`Completion summary for ${item.title}`}
                  placeholder="Completion summary (required to mark complete)"
                  value={summaries[item.id] ?? item.completion_summary}
                  onChange={(event) =>
                    setSummaries((current) => ({ ...current, [item.id]: event.target.value }))
                  }
                />
                <div className="flex flex-wrap gap-2">
                  {statuses.map(([code, label]) => (
                    <Button
                      key={code}
                      type="button"
                      variant={item.status.toUpperCase() === label.toUpperCase() ? "default" : "outline"}
                      disabled={saving === item.id}
                      onClick={() => void updateItem(item, code)}
                    >
                      {saving === item.id ? "Saving…" : label}
                    </Button>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  );
}
