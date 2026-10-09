"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  Clock3,
  FilePlus2,
  FileText,
  Info,
  LoaderCircle,
  Search,
} from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RequestPagination, REQUESTS_PER_PAGE } from "@/components/request-pagination";
import type { DSRRequest } from "@/lib/types";

interface PatientProps {
  principalId: string;
}

const requestTypes = [
  ["ACCESS", "Access"],
  ["PORTABILITY", "Portability"],
  ["RECTIFICATION", "Rectification"],
  ["ERASURE", "Erasure"],
  ["RESTRICTION", "Restriction"],
  ["OBJECTION", "Objection"],
];

function badgeVariant(state: string): "default" | "secondary" | "destructive" | "outline" {
  if (state === "Escalated" || state === "Rejected") return "destructive";
  if (state === "Closed") return "secondary";
  if (state === "Waiting for Subject Information") return "outline";
  return "default";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

export function PatientDashboard({ principalId }: PatientProps) {
  const [requests, setRequests] = useState<DSRRequest[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [requestType, setRequestType] = useState("ACCESS");
  const [description, setDescription] = useState("");
  const [department, setDepartment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    async function loadRequests() {
      try {
        const response = await fetch("/api/requests", { cache: "no-store" });
        const body = (await response.json()) as DSRRequest[] | { detail?: string };
        if (!response.ok) {
          throw new Error("detail" in body ? body.detail : "Could not load your requests.");
        }
        if (!cancelled) setRequests(body as DSRRequest[]);
      } catch (error) {
        if (!cancelled) {
          toast.error(error instanceof Error ? error.message : "Could not load your requests.");
          setRequests(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadRequests();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const visibleRequests = useMemo(() => {
    const items = requests ?? [];
    const term = search.trim().toLowerCase();
    return term
      ? items.filter((item) =>
          [item.id.toString(), item.request_type, item.state, item.description]
            .join(" ")
            .toLowerCase()
            .includes(term),
        )
      : items;
  }, [requests, search]);

  const activeCount = (requests ?? []).filter(
    (item) => item.closed_at === null && item.state !== "Rejected" && item.state !== "Withdrawn",
  ).length;
  const waitingCount = (requests ?? []).filter(
    (item) => item.state === "Waiting for Subject Information",
  ).length;
  const completedCount = (requests ?? []).filter((item) => item.closed_at !== null).length;
  const pageCount = Math.max(1, Math.ceil(visibleRequests.length / REQUESTS_PER_PAGE));
  const currentPage = Math.min(page, pageCount);
  const paginatedRequests = visibleRequests.slice(
    (currentPage - 1) * REQUESTS_PER_PAGE,
    currentPage * REQUESTS_PER_PAGE,
  );

  async function submitRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!description.trim()) {
      toast.error("Describe what your request is about.");
      return;
    }
    setSubmitting(true);
    try {
      const response = await fetch("/api/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_type: requestType,
          description: description.trim(),
          department: department.trim(),
        }),
      });
      const body = (await response.json()) as DSRRequest | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in body ? body.detail : "Request could not be submitted.");
      }
      toast.success("Your mock request was submitted.");
      setDescription("");
      setDepartment("");
      setRefresh((current) => current + 1);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Request could not be submitted.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AppShell role="patient" principalId={principalId}>
      <div className="space-y-7">
        <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">PATIENT PORTAL</p>
            <h1 className="text-3xl font-semibold tracking-tight md:text-[34px]">Your requests</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Follow the status of your data requests and submit a new request using fictional information.
            </p>
          </div>
          <a href="#new-request" className="inline-flex h-9 items-center justify-center gap-2 self-start rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground transition hover:bg-primary/90 sm:self-auto">
            <FilePlus2 className="size-4" />
            New request
          </a>
        </section>

        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard label="Total requests" value={loading ? "—" : requests?.length ?? 0} note="Submitted in this workspace" icon={FileText} />
          <StatCard label="In progress" value={loading ? "—" : activeCount} note="Awaiting review or your input" icon={Clock3} accent="blue" />
          <StatCard label="Completed" value={loading ? "—" : completedCount} note="Closed requests" icon={CheckCircle2} accent="teal" />
        </div>

        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,.8fr)]">
          <Card className="order-2 border-0 shadow-sm ring-1 ring-border/80 xl:order-1">
            <CardHeader className="gap-3 border-b pb-5 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle className="text-lg">Request history</CardTitle>
                <CardDescription className="mt-1">Status, target date, and published response for your cases.</CardDescription>
              </div>
              <label className="relative sm:w-56">
                <span className="sr-only">Search your requests</span>
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Find a request…"
                  className="h-9 pl-9"
                />
              </label>
            </CardHeader>
            <CardContent className="space-y-4 pt-5">
              {loading ? (
                <div className="space-y-3">
                  <Skeleton className="h-28 w-full" />
                  <Skeleton className="h-28 w-full" />
                </div>
              ) : requests === null ? (
                <div className="rounded-xl border border-dashed p-8 text-center">
                  <CircleHelp className="mx-auto mb-3 size-8 text-muted-foreground" />
                  <p className="font-medium">Requests could not be loaded</p>
                  <Button variant="outline" className="mt-4" onClick={() => setRefresh((current) => current + 1)}>Try again</Button>
                </div>
              ) : visibleRequests.length === 0 ? (
                <div className="rounded-xl border border-dashed p-8 text-center">
                  <FileText className="mx-auto mb-3 size-8 text-muted-foreground" />
                  <p className="font-medium">{requests.length ? "No matching requests" : "No requests yet"}</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {requests.length ? "Try a different search." : "When you submit a request, it will appear here."}
                  </p>
                </div>
              ) : (
                <>
                  <div className="overflow-hidden rounded-xl border">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/55 hover:bg-muted/55">
                          <TableHead className="w-12">#</TableHead>
                          <TableHead>Request</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Submitted</TableHead>
                          <TableHead>Response target</TableHead>
                          <TableHead className="text-right">Action</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {paginatedRequests.map((item, index) => (
                          <PatientRequestRow
                            key={item.id}
                            item={item}
                            number={(currentPage - 1) * REQUESTS_PER_PAGE + index + 1}
                          />
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                  <RequestPagination
                    page={currentPage}
                    totalItems={visibleRequests.length}
                    onPageChange={setPage}
                  />
                  <p className="text-xs text-muted-foreground">
                    Showing {paginatedRequests.length} of {visibleRequests.length} matching requests.
                  </p>
                </>
              )}
            </CardContent>
          </Card>

          <Card id="new-request" className="order-1 scroll-mt-24 border-0 shadow-sm ring-1 ring-border/80 xl:order-2">
            <CardHeader className="border-b pb-5">
              <div className="mb-1 flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <FilePlus2 className="size-5" />
              </div>
              <CardTitle className="text-lg">Submit a request</CardTitle>
              <CardDescription>Use fictional details only. Do not include clinical information.</CardDescription>
            </CardHeader>
            <CardContent className="pt-5">
              <form onSubmit={submitRequest} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="request-type">Request type</Label>
                  <select
                    id="request-type"
                    value={requestType}
                    onChange={(event) => setRequestType(event.target.value)}
                    className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    {requestTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="request-description">What is your request about?</Label>
                  <Textarea
                    id="request-description"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="Describe the request in general terms. Do not enter real health details."
                    required
                    className="min-h-32"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="request-department">Department <span className="font-normal text-muted-foreground">(optional)</span></Label>
                  <Input id="request-department" value={department} onChange={(event) => setDepartment(event.target.value)} maxLength={128} placeholder="e.g. Records" />
                </div>
                <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950">
                  <Info className="mt-0.5 size-4 shrink-0" />
                  This is a demo. Never submit real patient identifiers or medical details.
                </div>
                <Button type="submit" className="w-full gap-2" disabled={submitting}>
                  {submitting ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowRight className="size-4" />}
                  {submitting ? "Submitting…" : "Submit mock request"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
        {waitingCount > 0 && (
          <div className="flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-950">
            <CircleHelp className="mt-0.5 size-5 shrink-0" />
            <div>
              <p className="font-semibold">{waitingCount} request{waitingCount === 1 ? " is" : "s are"} waiting for your information</p>
              <p className="mt-1 text-sky-900/80">Open the existing patient workspace to provide a clarification response.</p>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}

function PatientRequestRow({ item, number }: { item: DSRRequest; number: number }) {
  const overdue = item.time_remaining_seconds <= 0 && item.closed_at === null;
  return (
    <TableRow>
      <TableCell className="font-mono text-xs text-muted-foreground">{number}</TableCell>
      <TableCell className="min-w-[220px]">
        <p className="font-semibold">DSR-{item.id} · {item.request_type}</p>
        <p className="mt-1 line-clamp-2 max-w-[360px] text-xs leading-5 text-muted-foreground">
          {item.description}
        </p>
        {item.closed_at && (
          <p className="mt-1 text-xs text-muted-foreground">
            Resolution: {item.resolution_status}
          </p>
        )}
      </TableCell>
      <TableCell>
        <Badge variant={badgeVariant(item.state)}>{item.state}</Badge>
      </TableCell>
      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
        {formatDate(item.submitted_at)}
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <p className={`text-sm font-medium ${overdue ? "text-rose-700" : ""}`}>
          {formatDate(item.response_due_at)}
        </p>
        <p className={`mt-1 text-xs ${overdue ? "text-rose-700" : "text-muted-foreground"}`}>
          {overdue ? "Overdue" : "Within target"}
        </p>
      </TableCell>
      <TableCell className="text-right">
        <Link
          href={`/patient/requests/${item.id}`}
          className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
        >
          View
        </Link>
      </TableCell>
    </TableRow>
  );
}
