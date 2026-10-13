"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowDownUp,
  Building2,
  CalendarClock,
  CheckCircle2,
  CircleUserRound,
  ClipboardCheck,
  Download,
  ListFilter,
  Search,
  ShieldAlert,
  UsersRound,
  X,
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { DPOQueueResponse, DSRRequest } from "@/lib/types";
import { RequestPagination, REQUESTS_PER_PAGE } from "@/components/request-pagination";
import { formatSlaDue } from "@/lib/format-sla";
import { useCurrentTime } from "@/hooks/use-current-time";

interface DPOProps {
  principalId: string;
  initialFilters?: {
    state: string;
    queue: string;
    department: string;
    assignedTo?: string;
  };
}

const states = [
  ["", "All statuses"],
  ["SUBMITTED", "Submitted"],
  ["IN_PROGRESS", "In Progress"],
  ["COMPLETED", "Completed"],
];

const requestTypes = [
  ["", "All request types"],
  ["ACCESS", "Access / Copy"],
  ["PORTABILITY", "Data transfer / Portability"],
  ["RECTIFICATION", "Rectification / Correction"],
  ["ERASURE", "Deletion / Erasure"],
  ["RESTRICTION", "Restriction"],
  ["OBJECTION", "Objection"],
  ["OTHER", "Other"],
];

const queues = [
  ["", "All cases"],
  ["overdue", "Overdue"],
  ["due_soon", "Due in the next 7 days"],
  ["unassigned", "Unassigned"],
  ["waiting", "Waiting for patient"],
];

const queuePresets = [
  { value: "mine", label: "Assigned to me" },
  { value: "overdue", label: "Overdue" },
  { value: "unassigned", label: "Unassigned" },
  { value: "waiting", label: "Waiting for patient" },
] as const;

function statusVariant(state: string): "default" | "secondary" | "destructive" | "outline" {
  if (state === "Completed") return "secondary";
  if (state === "In Progress") return "default";
  return "outline";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

export function DpoDashboard({
  principalId,
  initialFilters = { state: "", queue: "", department: "" },
}: DPOProps) {
  const [data, setData] = useState<DPOQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [state, setState] = useState(initialFilters.state);
  const [requestType, setRequestType] = useState("");
  const [queue, setQueue] = useState(initialFilters.queue);
  const [department, setDepartment] = useState(initialFilters.department);
  const [assignedTo, setAssignedTo] = useState(initialFilters.assignedTo ?? "");
  const [search, setSearch] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState(1);
  const currentTime = useCurrentTime();

  useEffect(() => {
    let cancelled = false;
    async function loadRequests() {
    setLoading(true);
    const params = new URLSearchParams();
    if (state) params.set("state", state);
    if (requestType) params.set("type", requestType);
    if (department.trim()) params.set("department", department.trim());
    if (queue) params.set("queue", queue);
    if (assignedTo.trim()) params.set("assigned_to", assignedTo.trim());
    try {
      const response = await fetch(`/api/requests?${params.toString()}`, {
        cache: "no-store",
      });
      const body = (await response.json()) as DPOQueueResponse | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in body ? body.detail : "Could not load the DPO queue.");
      }
      if (!cancelled) setData(body as DPOQueueResponse);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Could not load the DPO queue.";
      if (!cancelled) {
        toast.error(message);
        setData(null);
      }
    } finally {
      if (!cancelled) setLoading(false);
    }
    }
    void loadRequests();
    return () => {
      cancelled = true;
    };
  }, [state, requestType, department, queue, assignedTo, refresh]);

  const visibleRequests = useMemo(() => {
    const requests = data?.requests ?? [];
    const term = search.trim().toLowerCase();
    if (!term) return requests;
    return requests.filter((item) =>
      [
        item.id.toString(),
        `dsr-${item.id}`,
        item.description,
        item.department,
        item.assigned_to,
        item.request_type,
      ]
        .join(" ")
        .toLowerCase()
        .includes(term),
    );
  }, [data, search]);

  const requests = data?.requests ?? [];
  const pageCount = Math.max(1, Math.ceil(visibleRequests.length / REQUESTS_PER_PAGE));
  const currentPage = Math.min(page, pageCount);
  const paginatedRequests = visibleRequests.slice(
    (currentPage - 1) * REQUESTS_PER_PAGE,
    currentPage * REQUESTS_PER_PAGE,
  );
  const overdueCount = requests.filter(
    (item) =>
      item.state !== "Completed" &&
      item.closed_at === null &&
      (item.time_remaining_seconds <= 0 ||
        item.legal_time_remaining_seconds === 0),
  ).length;
  const unassignedCount = requests.filter(
    (item) =>
      !item.assigned_to &&
      item.closed_at === null &&
      item.state !== "Completed",
  ).length;
  const inReviewCount = requests.filter(
    (item) =>
      item.sub_status === "Privacy Review" ||
      item.sub_status === "Legal / Management Review",
  ).length;
  const activeFilters = [
    ...(search.trim() ? [{ label: `Search: ${search.trim()}`, clear: () => { setSearch(""); setPage(1); } }] : []),
    ...(state ? [{ label: `Status: ${states.find(([value]) => value === state)?.[1] ?? state}`, clear: () => { setState(""); setPage(1); } }] : []),
    ...(requestType ? [{ label: `Type: ${requestTypes.find(([value]) => value === requestType)?.[1] ?? requestType}`, clear: () => { setRequestType(""); setPage(1); } }] : []),
    ...(queue ? [{ label: `Queue: ${queues.find(([value]) => value === queue)?.[1] ?? queue}`, clear: () => { setQueue(""); setPage(1); } }] : []),
    ...(department.trim() ? [{ label: `Department: ${department.trim()}`, clear: () => { setDepartment(""); setPage(1); } }] : []),
    ...(assignedTo.trim() ? [{ label: `Reviewer: ${assignedTo.trim()}`, clear: () => { setAssignedTo(""); setPage(1); } }] : []),
  ];

  function applyPreset(preset: "mine" | "overdue" | "unassigned" | "waiting") {
    setState("");
    setRequestType("");
    setQueue(
      preset === "overdue" || preset === "unassigned" || preset === "waiting"
        ? preset
        : "",
    );
    setAssignedTo(preset === "mine" ? principalId : "");
    setDepartment("");
    setSearch("");
    setPage(1);
  }

  function clearFilters() {
    setState("");
    setRequestType("");
    setQueue("");
    setDepartment("");
    setAssignedTo("");
    setSearch("");
    setPage(1);
  }

  return (
    <AppShell role="dpo" principalId={principalId}>
      <div className="space-y-7">
        <section className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
              PRIVACY OPERATIONS
            </p>
            <h1 className="text-3xl font-semibold tracking-tight md:text-[34px]">
              Request queue
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Review workload, identify cases needing attention, and manage each request through its case workspace.
            </p>
          </div>
          <Button variant="outline" onClick={() => { setLoading(true); setRefresh((current) => current + 1); }} className="gap-2 self-start sm:self-auto">
            <ArrowDownUp className="size-4" />
            Refresh queue
          </Button>
        </section>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Visible cases" value={loading ? "—" : requests.length} note="Based on the current filters" icon={ClipboardCheck} />
          <StatCard label="Overdue" value={loading ? "—" : overdueCount} note="Response target has passed" icon={CalendarClock} accent="red" />
          <StatCard label="Unassigned" value={loading ? "—" : unassignedCount} note="No reviewer assigned" icon={UsersRound} accent="amber" />
          <StatCard label="In review" value={loading ? "—" : inReviewCount} note="Privacy or legal review" icon={CheckCircle2} accent="blue" />
        </div>

        <section aria-label="Quick queue views" className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-sm font-medium">Quick views:</span>
          {queuePresets.map(({ value, label }) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant="outline"
              onClick={() => applyPreset(value)}
            >
              {label}
            </Button>
          ))}
        </section>

        <Card className="border-0 shadow-sm ring-1 ring-border/80">
          <CardHeader className="gap-3 border-b pb-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle className="text-lg">All requests</CardTitle>
              <CardDescription className="mt-1">
                Filters apply to the DPO queue; search narrows the visible results.
              </CardDescription>
            </div>
            <Badge variant="outline" className="w-fit gap-1.5 px-2.5 py-1">
              <ShieldAlert className="size-3.5" />
              Mock records only
            </Badge>
          </CardHeader>
          <CardContent className="space-y-4 pt-5">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
              <label className="relative md:col-span-2 xl:col-span-2">
                <span className="sr-only">Search cases</span>
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Search ID, type, department, reviewer…"
                  className="h-10 pl-9"
                />
              </label>
              <FilterSelect label="Status" value={state} options={states} onChange={(value) => { setState(value); setPage(1); }} />
              <FilterSelect label="Request type" value={requestType} options={requestTypes} onChange={(value) => { setRequestType(value); setPage(1); }} />
              <FilterSelect label="Queue" value={queue} options={queues} onChange={(value) => { setQueue(value); setPage(1); }} />
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_auto]">
              <label className="relative">
                <span className="sr-only">Filter by assigned reviewer</span>
                <CircleUserRound className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={assignedTo}
                  onChange={(event) => {
                    setAssignedTo(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Filter by assigned staff ID"
                  className="h-10 pl-9"
                />
              </label>
              <label className="relative">
                <span className="sr-only">Filter by department</span>
                <Building2 className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={department}
                  onChange={(event) => {
                    setDepartment(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Filter by department"
                  className="h-10 pl-9"
                />
              </label>
              <Button variant="secondary" className="gap-2" disabled title="CSV export will be added with the case-detail workflow">
                <Download className="size-4" />
                Export
              </Button>
            </div>

            {activeFilters.length > 0 && (
              <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
                <span className="text-xs font-medium text-muted-foreground">Active filters:</span>
                {activeFilters.map((filter) => (
                  <button
                    key={filter.label}
                    type="button"
                    onClick={filter.clear}
                    className="inline-flex min-h-8 items-center gap-1 rounded-full border bg-background px-3 text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    aria-label={`Remove ${filter.label} filter`}
                  >
                    {filter.label}
                    <X aria-hidden="true" className="size-3.5" />
                  </button>
                ))}
                <Button type="button" variant="ghost" size="sm" onClick={clearFilters}>
                  Reset all
                </Button>
              </div>
            )}

            {loading ? (
              <div className="space-y-3 py-2">
                <Skeleton className="h-11 w-full" />
                <Skeleton className="h-14 w-full" />
                <Skeleton className="h-14 w-full" />
              </div>
            ) : !data ? (
              <div className="flex flex-col items-center rounded-xl border border-dashed py-12 text-center">
                <AlertCircle className="mb-3 size-8 text-rose-600" />
                <p className="font-medium">Queue unavailable</p>
                <p className="mt-1 text-sm text-muted-foreground">Check the Django web service, then refresh.</p>
                <Button onClick={() => { setLoading(true); setRefresh((current) => current + 1); }} className="mt-4">Try again</Button>
              </div>
            ) : visibleRequests.length === 0 ? (
              <div className="flex flex-col items-center rounded-xl border border-dashed py-12 text-center">
                <ListFilter className="mb-3 size-8 text-muted-foreground" />
                <p className="font-medium">
                  {requests.length === 0 && activeFilters.length === 0
                    ? "No requests have been submitted yet"
                    : "No cases match these filters"}
                </p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {requests.length === 0 && activeFilters.length === 0
                    ? "New requests will appear here."
                    : "Remove a filter or try a different search."}
                </p>
                {activeFilters.length > 0 && (
                  <Button variant="outline" className="mt-4" onClick={clearFilters}>
                    Clear filters
                  </Button>
                )}
              </div>
            ) : (
              <div
                role="region"
                aria-label="Request queue results"
                tabIndex={0}
                className="overflow-x-auto rounded-xl border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <Table className="min-w-[900px]">
                  <TableHeader>
                    <TableRow className="bg-muted/55 hover:bg-muted/55">
                      <TableHead className="w-12">#</TableHead>
                      <TableHead>Request</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Department / reviewer</TableHead>
                      <TableHead>Submitted</TableHead>
                      <TableHead>Response target</TableHead>
                      <TableHead>Reference</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {paginatedRequests.map((item, index) => (
                      <QueueRow
                        key={item.id}
                        item={item}
                        currentTime={currentTime}
                        number={(currentPage - 1) * REQUESTS_PER_PAGE + index + 1}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
            {!loading && data && visibleRequests.length > 0 && (
              <RequestPagination
                page={currentPage}
                totalItems={visibleRequests.length}
                onPageChange={setPage}
              />
            )}
            <p className="text-xs text-muted-foreground" aria-live="polite">
              Showing {paginatedRequests.length} of {visibleRequests.length} matching requests ({requests.length} loaded). Open a request to review its details, assignment, workflow actions, and activity.
            </p>
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[][];
  onChange: (value: string) => void;
}) {
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full appearance-none rounded-lg border border-input bg-white px-3 pr-8 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </label>
  );
}

function QueueRow({
  item,
  number,
  currentTime,
}: {
  item: DSRRequest;
  number: number;
  currentTime: number | null;
}) {
  const overdue =
    (item.time_remaining_seconds <= 0 ||
      item.legal_time_remaining_seconds === 0) &&
    item.closed_at === null &&
    item.state !== "Completed";
  const slaLabel =
    item.closed_at === null &&
    item.state !== "Completed"
      ? formatSlaDue(
          item.response_due_at,
          currentTime,
          item.time_remaining_seconds,
        )
      : "Target date";
  return (
    <TableRow>
      <TableCell className="font-mono text-xs text-muted-foreground">{number}</TableCell>
      <TableCell className="min-w-[210px]">
        <Link
          href={`/dpo/requests/${item.id}`}
          className="font-semibold text-primary hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          DSR-{item.id} · {item.request_type_label ?? item.request_type}
        </Link>
        <p className="mt-1 line-clamp-2 max-w-[320px] text-xs leading-5 text-muted-foreground">{item.description}</p>
      </TableCell>
      <TableCell>
        <Badge variant={statusVariant(item.state)}>{item.state}</Badge>
        {item.sub_status && (
          <p className="mt-1 text-xs text-muted-foreground">{item.sub_status}</p>
        )}
      </TableCell>
      <TableCell className="min-w-[160px]">
        <p className="text-sm">{item.department || "Unassigned department"}</p>
        <p className="mt-1 text-xs text-muted-foreground">{item.assigned_to || "No reviewer assigned"}</p>
      </TableCell>
      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">{formatDate(item.submitted_at)}</TableCell>
      <TableCell className="whitespace-nowrap">
        <p className={`text-sm font-medium ${overdue ? "text-rose-700" : ""}`}>{formatDate(item.response_due_at)}</p>
        <p className={`mt-1 text-xs ${overdue ? "font-medium text-rose-700" : "text-muted-foreground"}`}>
          Internal target · {slaLabel}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Legal deadline:{" "}
          {item.legal_deadline_at ? formatDate(item.legal_deadline_at) : "Not configured"}
        </p>
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">
        DSR-{item.id}
      </TableCell>
      <TableCell className="text-right">
        <Link
          href={`/dpo/requests/${item.id}`}
          className="inline-flex h-8 items-center justify-center rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
        >
          View
        </Link>
      </TableCell>
    </TableRow>
  );
}
