"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  CalendarClock,
  CircleHelp,
  LoaderCircle,
  OctagonX,
} from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
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
import { Textarea } from "@/components/ui/textarea";
import type { PatientRequestDetailResponse } from "@/lib/types";

function badgeVariant(
  state: string,
): "default" | "secondary" | "destructive" | "outline" {
  if (state === "Escalated" || state === "Rejected") return "destructive";
  if (state === "Closed" || state === "Withdrawn") return "secondary";
  if (state === "Waiting for Subject Information") return "outline";
  return "default";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

async function fetchRequest(id: number): Promise<PatientRequestDetailResponse> {
  const response = await fetch(`/api/requests/${id}`, { cache: "no-store" });
  const body = (await response.json()) as
    | PatientRequestDetailResponse
    | { detail?: string };
  if (!response.ok || !("can_withdraw" in body)) {
    throw new Error("detail" in body ? body.detail : "Could not load this request.");
  }
  return body;
}

export function PatientRequestDetail({
  id,
  principalId,
}: {
  id: number;
  principalId: string;
}) {
  const [data, setData] = useState<PatientRequestDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [reason, setReason] = useState("");
  const [withdrawing, setWithdrawing] = useState(false);

  const loadRequest = useCallback(async () => {
    setLoadError("");
    try {
      setData(await fetchRequest(id));
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Could not load this request.",
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    async function loadInitialRequest() {
      try {
        const request = await fetchRequest(id);
        if (!cancelled) setData(request);
      } catch (error) {
        if (!cancelled) {
          setLoadError(
            error instanceof Error ? error.message : "Could not load this request.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadInitialRequest();
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function withdrawRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setWithdrawing(true);
    try {
      const response = await fetch(`/api/requests/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) {
        throw new Error(body.detail ?? "This request could not be withdrawn.");
      }
      toast.success("Your request has been withdrawn.");
      setReason("");
      await loadRequest();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "This request could not be withdrawn.",
      );
    } finally {
      setWithdrawing(false);
    }
  }

  const overdue =
    !!data && data.time_remaining_seconds <= 0 && data.closed_at === null;

  return (
    <AppShell role="patient" principalId={principalId}>
      <div className="space-y-6">
        <Link
          href="/patient"
          className="inline-flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
          Back to your requests
        </Link>

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : loadError || !data ? (
          <Card>
            <CardContent className="flex flex-col items-center py-12 text-center">
              <AlertCircle className="mb-3 size-8 text-rose-600" />
              <h1 className="font-semibold">Request unavailable</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {loadError || "This request could not be found."}
              </p>
              <Button onClick={() => void loadRequest()} className="mt-4">
                Try again
              </Button>
            </CardContent>
          </Card>
        ) : (
          <>
            <Card className="border-0 shadow-sm ring-1 ring-border/80">
              <CardContent className="flex flex-col justify-between gap-5 pt-6 sm:flex-row sm:items-start">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                    YOUR REQUEST
                  </p>
                  <h1 className="mt-2 text-3xl font-semibold tracking-tight">
                    DSR-{data.id} · {data.request_type}
                  </h1>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Submitted {formatDate(data.submitted_at)}
                  </p>
                </div>
                <Badge variant={badgeVariant(data.state)}>{data.state}</Badge>
              </CardContent>
            </Card>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,1fr)]">
              <Card className="border-0 shadow-sm ring-1 ring-border/80">
                <CardHeader>
                  <CardTitle className="text-lg">Request details</CardTitle>
                  <CardDescription>
                    This page shows the status and information associated with your request.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <dl className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Department
                      </dt>
                      <dd className="mt-1 text-sm font-medium">
                        {data.department || "Not specified"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Resolution
                      </dt>
                      <dd className="mt-1 text-sm font-medium">
                        {data.resolution_status}
                      </dd>
                    </div>
                    <div className="sm:col-span-2">
                      <dt className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        <CalendarClock className="size-3.5" />
                        Response target
                      </dt>
                      <dd className={`mt-1 text-sm font-medium ${overdue ? "text-rose-700" : ""}`}>
                        {formatDate(data.response_due_at)}
                        {overdue ? " · Overdue" : ""}
                      </dd>
                    </div>
                  </dl>
                  <div className="border-t pt-5">
                    <h2 className="text-sm font-semibold">Description</h2>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                      {data.description}
                    </p>
                  </div>
                </CardContent>
              </Card>

              <Card className="h-fit border-0 shadow-sm ring-1 ring-border/80">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-lg">
                    <OctagonX className="size-4 text-rose-700" />
                    Withdraw request
                  </CardTitle>
                  <CardDescription>
                    You can withdraw a request while it is still active.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {data.can_withdraw ? (
                    <form onSubmit={withdrawRequest} className="space-y-3">
                      <label htmlFor="withdraw-reason" className="text-sm font-medium">
                        Reason <span className="font-normal text-muted-foreground">(optional)</span>
                      </label>
                      <Textarea
                        id="withdraw-reason"
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        maxLength={2000}
                        placeholder="You may add a reason for withdrawing"
                        disabled={withdrawing}
                      />
                      <Button
                        type="submit"
                        variant="destructive"
                        disabled={withdrawing}
                        className="gap-2"
                      >
                        {withdrawing && <LoaderCircle className="size-4 animate-spin" />}
                        {withdrawing ? "Withdrawing…" : "Withdraw request"}
                      </Button>
                    </form>
                  ) : (
                    <div className="flex gap-2 rounded-lg bg-muted/60 p-3 text-sm text-muted-foreground">
                      <CircleHelp className="mt-0.5 size-4 shrink-0" />
                      This request has reached a final status and can no longer be withdrawn.
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
