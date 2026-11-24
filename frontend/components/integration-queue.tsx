"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Cable, RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { IntegrationJob, IntegrationJobStatus, IntegrationJobsResponse } from "@/lib/types";

const VIEWS: Array<{ value: IntegrationJobStatus | ""; label: string }> = [
  { value: "FAILED", label: "Needs attention" },
  { value: "PENDING", label: "Waiting or retrying" },
  { value: "RUNNING", label: "Running" },
  { value: "SUCCEEDED", label: "Succeeded" },
  { value: "", label: "All" },
];

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

function statusVariant(status: IntegrationJobStatus): "default" | "secondary" | "destructive" | "outline" {
  if (status === "FAILED") return "destructive";
  if (status === "SUCCEEDED") return "secondary";
  return "outline";
}

async function fetchJobs(view: IntegrationJobStatus | ""): Promise<IntegrationJobsResponse> {
  const response = await fetch(`/api/integration-jobs${view ? `?status=${view}` : ""}`, {
    cache: "no-store",
  });
  const body = (await response.json()) as IntegrationJobsResponse | { detail?: string };
  if (!response.ok || !("jobs" in body)) {
    throw new Error("detail" in body && body.detail ? body.detail : "Could not load integration jobs.");
  }
  return body;
}

function targetLabel(job: IntegrationJob): string {
  if (job.kind === "PATIENT_NOTIFICATION") return "Patient";
  if (job.target === "DYNAMICS") return "Microsoft Dynamics 365";
  return job.target;
}

/**
 * Work sent to other systems: automated source searches and patient
 * notifications. Jobs retry on their own; the ones that give up land in
 * "Needs attention" for a person to retry or handle by hand.
 */
export function IntegrationQueue({ principalId }: { principalId: string }) {
  const [view, setView] = useState<IntegrationJobStatus | "">("FAILED");
  const [data, setData] = useState<IntegrationJobsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchJobs(view));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load integration jobs.");
    } finally {
      setLoading(false);
    }
  }, [view]);

  useEffect(() => {
    let cancelled = false;
    fetchJobs(view)
      .then((body) => {
        if (!cancelled) setData(body);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          toast.error(error instanceof Error ? error.message : "Could not load integration jobs.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [view]);

  async function retry(job: IntegrationJob) {
    setRetrying(job.id);
    try {
      const response = await fetch(`/api/integration-jobs/${job.id}/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not retry this job.");
      toast.success(`${job.kind_label} for ${targetLabel(job)} queued again.`);
      await load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not retry this job.");
    } finally {
      setRetrying(null);
    }
  }

  const failed = data?.counts.FAILED ?? 0;

  return (
    <AppShell role="dpo" principalId={principalId}>
      <div className="space-y-6">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
              Connected systems
            </p>
            <h1 className="mt-2 flex items-center gap-2 text-3xl font-semibold tracking-tight">
              <Cable className="size-6 text-primary" />
              Integrations
            </h1>
            <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
              Automated source searches and patient notifications. Jobs retry on their own with
              growing waits. A job that keeps failing stops here so a person can retry it or do the
              work by hand. All connectors in this prototype are mocks.
            </p>
          </div>
          <Button
            variant="outline"
            className="gap-2 self-start sm:self-auto"
            onClick={() => {
              setLoading(true);
              void load();
            }}
          >
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>

        {failed > 0 && (
          <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <p>
              <span className="font-semibold">{failed} job(s) need attention.</span> Their source
              searches are marked Blocked, which stops those cases at privacy review until the
              search is retried or recorded by hand.
            </p>
          </div>
        )}

        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Job status">
          {VIEWS.map((item) => {
            const count = item.value ? data?.counts[item.value] : undefined;
            const selected = view === item.value;
            return (
              <button
                key={item.label}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => {
                  setLoading(true);
                  setView(item.value);
                }}
                className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
                  selected ? "border-primary bg-primary text-primary-foreground" : "bg-white hover:bg-muted"
                }`}
              >
                {item.label}
                {count !== undefined && <span className="ml-1.5 tabular-nums opacity-80">{count}</span>}
              </button>
            );
          })}
        </div>

        <Card className="border-0 shadow-sm ring-1 ring-border/80">
          <CardHeader>
            <CardTitle className="text-lg">{VIEWS.find((item) => item.value === view)?.label}</CardTitle>
            <CardDescription>Newest first, up to 200 jobs.</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : !data || data.jobs.length === 0 ? (
              <p className="rounded-lg bg-muted/50 p-4 text-sm text-muted-foreground">
                {view === "FAILED" ? "Nothing needs attention." : "No jobs in this view."}
              </p>
            ) : (
              <ul className="divide-y">
                {data.jobs.map((job) => (
                  <li key={job.id} className="grid gap-3 py-4 first:pt-0 sm:grid-cols-[1fr_auto] sm:items-start">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium">
                          {job.kind_label} · {targetLabel(job)}
                        </p>
                        <Badge variant={statusVariant(job.status)}>{job.status_label}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        <Link href={`/dpo/requests/${job.request_id}`} className="font-medium text-primary hover:underline">
                          DSR-{job.request_id}
                        </Link>{" "}
                        · queued by {job.created_by} · {formatDate(job.created_at)} · attempt{" "}
                        <span className="tabular-nums">
                          {job.attempts} of {job.max_attempts}
                        </span>
                        {job.status === "PENDING" && job.attempts > 0
                          ? ` · next try ${formatDate(job.next_attempt_at)}`
                          : ""}
                      </p>
                      {job.last_error && (
                        <p className="break-words text-sm text-rose-700">Last error: {job.last_error}</p>
                      )}
                      {job.status === "SUCCEEDED" && typeof job.result.reference === "string" && (
                        <p className="text-sm text-muted-foreground">Reference: {job.result.reference}</p>
                      )}
                    </div>
                    {job.status === "FAILED" && (
                      <Button
                        variant="outline"
                        className="gap-2"
                        onClick={() => void retry(job)}
                        disabled={retrying !== null}
                      >
                        <RotateCcw className="size-4" />
                        {retrying === job.id ? "Queuing…" : "Retry"}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
