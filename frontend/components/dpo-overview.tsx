"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  ArrowDownUp,
  CalendarClock,
  CheckCircle2,
  CircleHelp,
  ClipboardList,
  Clock3,
  FileWarning,
  UsersRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { DPOQueueResponse, DSRRequest } from "@/lib/types";

const TERMINAL_STATES = new Set(["Closed", "Rejected", "Withdrawn"]);

const workflowStates = [
  ["ACCEPTED", "Accepted / Under Assessment"],
  ["DEPT_SEARCH", "Departmental Search"],
  ["PRIVACY_REVIEW", "Privacy Review"],
  ["LEGAL_REVIEW", "Legal / Management Review"],
  ["RESPONSE_PREP", "Response Preparation"],
  ["NEEDS_INFORMATION", "Waiting for Subject Information"],
  ["ESCALATED", "Escalated"],
];

function isActive(request: DSRRequest): boolean {
  return !TERMINAL_STATES.has(request.state);
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

function statusVariant(
  state: string,
): "default" | "secondary" | "destructive" | "outline" {
  if (state === "Escalated" || state === "Rejected") return "destructive";
  if (state === "Closed" || state === "Withdrawn") return "secondary";
  if (state === "Response Preparation") return "default";
  return "outline";
}

export function DpoOverview({ principalId }: { principalId: string }) {
  const [data, setData] = useState<DPOQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadOverview() {
      setLoading(true);
      setLoadError("");
      try {
        const response = await fetch("/api/requests", { cache: "no-store" });
        const body = (await response.json()) as
          | DPOQueueResponse
          | { detail?: string };
        if (!response.ok || !("requests" in body)) {
          throw new Error(
            "detail" in body ? body.detail : "Could not load the DPO overview.",
          );
        }
        if (!cancelled) setData(body);
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Could not load the DPO overview.";
        if (!cancelled) {
          setLoadError(message);
          toast.error(message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadOverview();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const requests = useMemo(() => data?.requests ?? [], [data]);
  const activeRequests = useMemo(() => requests.filter(isActive), [requests]);
  const overdue = activeRequests.filter(
    (item) => item.time_remaining_seconds <= 0,
  );
  const unassigned = activeRequests.filter((item) => !item.assigned_to);
  const waitingForInformation = activeRequests.filter(
    (item) => item.state === "Waiting for Subject Information",
  );
  const dueSoon = activeRequests.filter(
    (item) =>
      item.time_remaining_seconds > 0 &&
      item.time_remaining_seconds <= 7 * 24 * 60 * 60,
  );

  const statusCounts = useMemo(
    () =>
      workflowStates
        .map(([value, label]) => ({
          value,
          label,
          count: activeRequests.filter((item) => item.state === label).length,
        }))
        .filter((item) => item.count > 0)
        .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)),
    [activeRequests],
  );

  const departmentCounts = useMemo(
    () => {
      const counts = new Map<string, number>();
      for (const request of activeRequests) {
        const department = request.department.trim();
        if (!department) continue;
        counts.set(department, (counts.get(department) ?? 0) + 1);
      }
      return [...counts.entries()]
        .map(([department, count]) => ({ department, count }))
        .sort((a, b) => b.count - a.count || a.department.localeCompare(b.department))
        .slice(0, 5);
    },
    [activeRequests],
  );

  const attentionRequests = useMemo(() => {
    return activeRequests
      .map((request) => {
        const isOverdue = request.time_remaining_seconds <= 0;
        const isUnassigned = !request.assigned_to;
        const isDueSoon =
          request.time_remaining_seconds > 0 &&
          request.time_remaining_seconds <= 7 * 24 * 60 * 60;
        return {
          request,
          reasons: [
            ...(isOverdue ? ["Overdue"] : []),
            ...(isUnassigned ? ["Unassigned"] : []),
            ...(!isOverdue && isDueSoon ? ["Due soon"] : []),
          ],
          priority: isOverdue ? 0 : isUnassigned ? 1 : isDueSoon ? 2 : 3,
        };
      })
      .filter((item) => item.reasons.length > 0)
      .sort(
        (a, b) =>
          a.priority - b.priority ||
          a.request.time_remaining_seconds - b.request.time_remaining_seconds,
      )
      .slice(0, 6);
  }, [activeRequests]);

  const recentRequests = useMemo(
    () =>
      [...requests]
        .sort(
          (a, b) =>
            new Date(b.submitted_at).getTime() -
            new Date(a.submitted_at).getTime(),
        )
        .slice(0, 5),
    [requests],
  );

  const maxStatusCount = Math.max(1, ...statusCounts.map((item) => item.count));
  const maxDepartmentCount = Math.max(
    1,
    ...departmentCounts.map((item) => item.count),
  );

  return (
    <AppShell role="dpo" principalId={principalId}>
      <div className="space-y-7">
        <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
              PRIVACY OPERATIONS
            </p>
            <h1 className="text-3xl font-semibold tracking-tight md:text-[34px]">
              DPO overview
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              A snapshot of your request workload and the cases that need attention.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => setRefresh((current) => current + 1)}
              disabled={loading}
              className="gap-2"
            >
              <ArrowDownUp className="size-4" />
              Refresh overview
            </Button>
            <Link
              href="/dpo/requests"
              className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/80"
            >
              <ClipboardList className="size-4" />
              Open full queue
            </Link>
          </div>
        </section>

        {loadError && !data ? (
          <Card>
            <CardContent className="flex flex-col items-center py-12 text-center">
              <AlertCircle className="mb-3 size-8 text-rose-600" />
              <h2 className="font-semibold">Overview unavailable</h2>
              <p className="mt-1 text-sm text-muted-foreground">{loadError}</p>
              <Button
                onClick={() => setRefresh((current) => current + 1)}
                className="mt-4"
              >
                Try again
              </Button>
            </CardContent>
          </Card>
        ) : (
          <>
            <section
              aria-label="Request metrics"
              className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5"
            >
              <MetricLink
                href="/dpo/requests"
                label="Active requests"
                value={loading ? "—" : activeRequests.length}
                note="Still being processed"
                icon={ClipboardList}
                accent="blue"
              />
              <MetricLink
                href="/dpo/requests?queue=overdue"
                label="Overdue"
                value={loading ? "—" : overdue.length}
                note="Response target passed"
                icon={CalendarClock}
                accent="red"
              />
              <MetricLink
                href="/dpo/requests?queue=unassigned"
                label="Unassigned"
                value={loading ? "—" : unassigned.length}
                note="Needs a reviewer"
                icon={UsersRound}
                accent="amber"
              />
              <MetricLink
                href="/dpo/requests?state=NEEDS_INFORMATION"
                label="Need information"
                value={loading ? "—" : waitingForInformation.length}
                note="Awaiting patient reply"
                icon={CircleHelp}
                accent="blue"
              />
              <MetricLink
                href="/dpo/requests?queue=due_soon"
                label="Due in 7 days"
                value={loading ? "—" : dueSoon.length}
                note="Response targets Due"
                icon={Clock3}
                accent="teal"
              />
            </section>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,.8fr)]">
              <Card className="border-0 shadow-sm ring-1 ring-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">Needs attention</CardTitle>
                  <CardDescription>
                    Highest-priority cases first: overdue, unassigned, then due soon.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {loading ? (
                    <div className="space-y-3">
                      <Skeleton className="h-16 w-full" />
                      <Skeleton className="h-16 w-full" />
                      <Skeleton className="h-16 w-full" />
                    </div>
                  ) : attentionRequests.length === 0 ? (
                    <div className="flex items-start gap-3 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-900">
                      <CheckCircle2 className="mt-0.5 size-5 shrink-0" />
                      <p>No urgent requests right now. Check the full queue for all open work.</p>
                    </div>
                  ) : (
                    <ul className="divide-y">
                      {attentionRequests.map(({ request, reasons }) => (
                        <li
                          key={request.id}
                          className="flex flex-col justify-between gap-3 py-4 first:pt-0 sm:flex-row sm:items-center"
                        >
                          <div className="min-w-0">
                            <Link
                              href={`/dpo/requests/${request.id}`}
                              className="font-semibold text-primary hover:underline"
                            >
                              DSR-{request.id} · {request.request_type}
                            </Link>
                            <p className="mt-1 truncate text-xs text-muted-foreground">
                              {request.department || "Unassigned department"} · Target{" "}
                              {formatDate(request.response_due_at)}
                            </p>
                            <div className="mt-2 flex flex-wrap gap-1.5">
                              {reasons.map((reason) => (
                                <Badge
                                  key={reason}
                                  variant={reason === "Overdue" ? "destructive" : "outline"}
                                >
                                  {reason}
                                </Badge>
                              ))}
                            </div>
                          </div>
                          <Link
                            href={`/dpo/requests/${request.id}`}
                            className="inline-flex h-7 items-center justify-center rounded-lg border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
                          >
                            View
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-4 border-t pt-4">
                    <Link
                      href="/dpo/requests"
                      className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
                    >
                      View all requests <ArrowRight className="size-4" />
                    </Link>
                  </div>
                </CardContent>
              </Card>

              <Card className="border-0 shadow-sm ring-1 ring-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">Workload breakdown</CardTitle>
                  <CardDescription>
                    Active work by workflow stage and department.
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-6 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                  <div>
                    <h2 className="mb-3 text-sm font-semibold">By status</h2>
                    {loading ? (
                      <div className="space-y-3">
                        <Skeleton className="h-7 w-full" />
                        <Skeleton className="h-7 w-full" />
                        <Skeleton className="h-7 w-full" />
                      </div>
                    ) : statusCounts.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No requests to summarize.</p>
                    ) : (
                      <ul className="space-y-3">
                        {statusCounts.slice(0, 6).map((item) => (
                          <li key={item.value}>
                            <Link
                              href={`/dpo/requests?state=${item.value}`}
                              className="group flex items-center justify-between gap-3 text-xs hover:text-primary"
                            >
                              <span className="truncate">{item.label}</span>
                              <span className="font-semibold tabular-nums">{item.count}</span>
                            </Link>
                            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-primary transition-all"
                                style={{
                                  width: `${Math.max(6, (item.count / maxStatusCount) * 100)}%`,
                                }}
                              />
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <h2 className="mb-3 text-sm font-semibold">By department</h2>
                    {loading ? (
                      <div className="space-y-3">
                        <Skeleton className="h-7 w-full" />
                        <Skeleton className="h-7 w-full" />
                        <Skeleton className="h-7 w-full" />
                      </div>
                    ) : departmentCounts.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No department workload yet.</p>
                    ) : (
                      <ul className="space-y-3">
                        {departmentCounts.map((item) => (
                          <li key={item.department}>
                            <Link
                              href={`/dpo/requests?department=${encodeURIComponent(item.department)}`}
                              className="group flex items-center justify-between gap-3 text-xs hover:text-primary"
                              title="View requests from this department in the queue"
                            >
                              <span className="truncate">{item.department}</span>
                              <span className="font-semibold tabular-nums">{item.count}</span>
                            </Link>
                            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-sky-600 transition-all"
                                style={{
                                  width: `${Math.max(6, (item.count / maxDepartmentCount) * 100)}%`,
                                }}
                              />
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </CardContent>
              </Card>
            </div>

            <Card className="border-0 shadow-sm ring-1 ring-border/80">
              <CardHeader className="flex flex-row items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">Recent requests</CardTitle>
                  <CardDescription className="mt-1">
                    Latest submissions. Open any case for its full workflow.
                  </CardDescription>
                </div>
                <FileWarning className="hidden size-5 text-muted-foreground sm:block" />
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-12 w-full" />
                  </div>
                ) : recentRequests.length === 0 ? (
                  <p className="py-5 text-sm text-muted-foreground">
                    No requests have been submitted yet.
                  </p>
                ) : (
                  <ul className="divide-y">
                    {recentRequests.map((request) => (
                      <li
                        key={request.id}
                        className="flex flex-col justify-between gap-2 py-3 first:pt-0 sm:flex-row sm:items-center"
                      >
                        <div className="min-w-0">
                          <Link
                            href={`/dpo/requests/${request.id}`}
                            className="font-medium text-primary hover:underline"
                          >
                            DSR-{request.id} · {request.request_type}
                          </Link>
                          <p className="mt-1 truncate text-xs text-muted-foreground">
                            {request.department || "Unassigned department"} · Submitted{" "}
                            {formatDate(request.submitted_at)}
                          </p>
                        </div>
                        <div className="flex items-center gap-3">
                          <Badge variant={statusVariant(request.state)}>{request.state}</Badge>
                          <Link
                            href={`/dpo/requests/${request.id}`}
                            className="text-xs font-semibold text-primary hover:underline"
                          >
                            View
                          </Link>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </AppShell>
  );
}

function MetricLink({
  href,
  label,
  value,
  note,
  icon: Icon,
  accent,
}: {
  href: string;
  label: string;
  value: string | number;
  note: string;
  icon: LucideIcon;
  accent: "teal" | "amber" | "red" | "blue";
}) {
  return (
    <Link
      href={href}
      className="rounded-xl transition hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <StatCard
        label={label}
        value={value}
        note={note}
        icon={Icon}
        accent={accent}
      />
    </Link>
  );
}
