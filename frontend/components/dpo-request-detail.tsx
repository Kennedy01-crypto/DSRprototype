"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowLeft,
  CalendarClock,
  History,
  LoaderCircle,
  MessageSquareText,
  Save,
  Send,
  UserRound,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { RequestProgress } from "@/components/request-progress";
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
import { Textarea } from "@/components/ui/textarea";
import { formatSlaDue } from "@/lib/format-sla";
import { useCurrentTime } from "@/hooks/use-current-time";
import type { DemoRole, DSRCaseDetailResponse } from "@/lib/types";

const requiredReasonActions = new Set([
  "request_information",
  "request_legal_review",
  "approve_legal_review",
  "return_from_legal_review",
  "flag_for_independent_review",
  "mark_for_duplicate",
  "resume_escalated",
  "reject",
  "escalate",
  "approve_and_close",
  "close_as_duplicate",
]);

const resolutions = [
  ["FULFILLED", "Fulfilled"],
  ["PARTIALLY_FULFILLED", "Partially fulfilled"],
  ["REJECTED", "Rejected (Refused)"],
  ["RETAINED", "Retained with rationale"],
  ["OUT_OF_SCOPE", "Out of scope"],
  ["UNABLE_TO_PROCESS", "Unable to process"],
];
const reviewOutcomes = [
  ...resolutions,
  ["DUPLICATE", "Duplicate"],
];
const workTypes = [
  ["DEPARTMENT_SEARCH", "Departmental search"],
  ["PRIVACY_REVIEW", "Privacy review"],
  ["LEGAL_MANAGEMENT", "Legal / management input"],
  ["RESPONSE_PREPARATION", "Response preparation"],
  ["OTHER", "Other"],
] as const;
const workStatuses = [
  ["OPEN", "Open"],
  ["IN_PROGRESS", "In progress"],
  ["COMPLETED", "Completed"],
  ["BLOCKED", "Blocked"],
] as const;

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

function statusVariant(state: string): "default" | "secondary" | "destructive" | "outline" {
  if (state === "Completed") return "secondary";
  if (state === "In Progress") return "default";
  return "outline";
}

async function fetchCase(id: number): Promise<DSRCaseDetailResponse> {
  const response = await fetch(`/api/requests/${id}`, { cache: "no-store" });
  const body = (await response.json()) as
    | DSRCaseDetailResponse
    | { detail?: string };
  if (!response.ok || !("request" in body)) {
    throw new Error("detail" in body ? body.detail : "Could not load this request.");
  }
  return body;
}

export function DpoRequestDetail({
  id,
  principalId,
  role = "dpo",
}: {
  id: number;
  principalId: string;
  role?: DemoRole;
}) {
  const [data, setData] = useState<DSRCaseDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [assignedTo, setAssignedTo] = useState("");
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [resolutionStatus, setResolutionStatus] = useState(resolutions[0][0]);
  const [proposedOutcome, setProposedOutcome] = useState(resolutions[0][0]);
  const [assignedReviewer, setAssignedReviewer] = useState("");
  const [duplicateOf, setDuplicateOf] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskType, setTaskType] = useState<(typeof workTypes)[number][0]>("DEPARTMENT_SEARCH");
  const [taskAssignee, setTaskAssignee] = useState("");
  const [taskDetails, setTaskDetails] = useState("");
  const [taskDueAt, setTaskDueAt] = useState("");
  const [taskRequired, setTaskRequired] = useState(false);
  const [taskSaving, setTaskSaving] = useState(false);
  const [exceptionType, setExceptionType] = useState("CLARIFICATION");
  const [exceptionReason, setExceptionReason] = useState("");
  const [exceptionPolicyReason, setExceptionPolicyReason] = useState("");
  const [patientNotification, setPatientNotification] = useState("");
  const [deadlineEffectDays, setDeadlineEffectDays] = useState("");
  const [exceptionSaving, setExceptionSaving] = useState(false);
  const [jurisdictionCode, setJurisdictionCode] = useState("KE");
  const [jurisdictionReason, setJurisdictionReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [sendingMessage, setSendingMessage] = useState(false);
  const [loadError, setLoadError] = useState("");
  const currentTime = useCurrentTime();

  const loadCase = useCallback(async (showLoading = true): Promise<DSRCaseDetailResponse | null> => {
    if (showLoading) setLoading(true);
    setLoadError("");
    try {
      const body = await fetchCase(id);
      setData(body);
      setAssignedTo(body.request.assigned_to);
      setJurisdictionCode(body.request.jurisdiction_code ?? "KE");
      setProposedOutcome(body.request.proposed_outcome ?? resolutions[0][0]);
      setAction((current) =>
        body.available_actions.some((item) => item.action === current)
          ? current
          : body.available_actions[0]?.action ?? "",
      );
      return body;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Could not load this request.";
      setLoadError(message);
      if (!showLoading) toast.error(message);
      return null;
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    async function loadInitialCase() {
      try {
        const body = await fetchCase(id);
        if (cancelled) return;
        setData(body);
        setAssignedTo(body.request.assigned_to);
        setJurisdictionCode(body.request.jurisdiction_code ?? "KE");
        setProposedOutcome(body.request.proposed_outcome ?? resolutions[0][0]);
        setAction(body.available_actions[0]?.action ?? "");
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
    void loadInitialCase();
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function submitMutation(
    event: FormEvent<HTMLFormElement>,
    method: "PATCH" | "POST",
    payload: object,
    successMessage: string,
  ) {
    event.preventDefault();
    if (method === "PATCH" && assignedTo.trim() !== data?.request.assigned_to) {
      const target = assignedTo.trim() || "unassigned";
      if (!window.confirm(`Change the assigned reviewer to ${target}?`)) return;
    }
    if (method === "POST") {
      const actionLabel = selectedAction?.label ?? "this workflow action";
      if (!window.confirm(`Apply “${actionLabel}” to DSR-${id}?`)) return;
    }
    setSaving(true);
    try {
      const response = await fetch(`/api/requests/${id}`, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) {
        throw new Error(body.detail ?? "The request could not be updated.");
      }
      const refreshedCase = await loadCase(false);
      toast.success(
        method === "POST"
          ? refreshedCase
            ? `Request status updated to ${refreshedCase.request.state}.`
            : "Workflow action applied."
          : successMessage,
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "The request could not be updated.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function sendPatientMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedMessage = message.trim();
    if (!trimmedMessage) return;
    setSendingMessage(true);
    try {
      const response = await fetch(`/api/requests/${id}/communications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmedMessage }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) {
        throw new Error(body.detail ?? "The message could not be sent.");
      }
      setMessage("");
      toast.success("Message added to the patient portal.");
      await loadCase(false);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "The message could not be sent.",
      );
    } finally {
      setSendingMessage(false);
    }
  }

  async function createWorkItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTaskSaving(true);
    try {
      const response = await fetch(`/api/requests/${id}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: taskTitle.trim(),
          task_type: taskType,
          assigned_to: taskAssignee.trim(),
          details: taskDetails.trim(),
          due_at: taskDueAt ? new Date(taskDueAt).toISOString() : null,
          required_for_completion: taskRequired,
        }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not create the work item.");
      setTaskTitle("");
      setTaskAssignee("");
      setTaskDetails("");
      setTaskDueAt("");
      setTaskRequired(false);
      toast.success("Parallel work item created.");
      await loadCase(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not create the work item.");
    } finally {
      setTaskSaving(false);
    }
  }

  async function updateWorkItem(event: FormEvent<HTMLFormElement>, itemId: number) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const status = String(form.get("status") ?? "");
    const completionSummary = String(form.get("completion_summary") ?? "").trim();
    if (status === "COMPLETED" && !completionSummary) {
      toast.error("Record a completion summary before completing this work item.");
      return;
    }
    setTaskSaving(true);
    try {
      const response = await fetch(`/api/requests/${id}/tasks/${itemId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, completion_summary: completionSummary }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not update the work item.");
      toast.success("Work item updated.");
      await loadCase(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update the work item.");
    } finally {
      setTaskSaving(false);
    }
  }

  async function createException(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setExceptionSaving(true);
    try {
      const response = await fetch(`/api/requests/${id}/exceptions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          exception_type: exceptionType,
          reason: exceptionReason.trim(),
          patient_notification: patientNotification.trim(),
          ...(exceptionType === "DEADLINE_PAUSE"
            ? { policy_reason: exceptionPolicyReason.trim() }
            : {}),
          ...(exceptionType === "EXTENSION" || exceptionType === "DEADLINE_PAUSE"
            ? { deadline_effect_days: Number(deadlineEffectDays) }
            : {}),
        }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not record the exception.");
      setExceptionReason("");
      setExceptionPolicyReason("");
      setPatientNotification("");
      setDeadlineEffectDays("");
      toast.success("Case exception recorded and audited.");
      await loadCase(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not record the exception.");
    } finally {
      setExceptionSaving(false);
    }
  }

  async function updateJurisdiction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const response = await fetch(`/api/requests/${id}/jurisdiction`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jurisdiction_code: jurisdictionCode.trim().toUpperCase(),
          reason: jurisdictionReason.trim(),
        }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not classify the jurisdiction.");
      setJurisdictionReason("");
      toast.success("Jurisdiction and deadline-policy snapshot updated.");
      await loadCase(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not classify the jurisdiction.");
    } finally {
      setSaving(false);
    }
  }

  const selectedAction = data?.available_actions.find(
    (item) => item.action === action,
  );
  const requiresReason = requiredReasonActions.has(action);
  const overdue =
    !!data &&
    (data.request.time_remaining_seconds <= 0 ||
      data.request.legal_time_remaining_seconds === 0) &&
    data.request.state !== "Completed";

  return (
    <AppShell role={role} principalId={principalId}>
      <div className="space-y-6">
        <Link
          href="/dpo/requests"
          className="inline-flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
          Back to request queue
        </Link>

        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-56 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : loadError || !data ? (
          <Card>
            <CardContent className="flex flex-col items-center py-12 text-center">
              <AlertCircle className="mb-3 size-8 text-rose-600" />
              <h1 className="font-semibold">Request unavailable</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {loadError || "This request could not be found."}
              </p>
              <Button onClick={() => void loadCase()} className="mt-4">
                Try again
              </Button>
            </CardContent>
          </Card>
        ) : (
          <>
            <RequestProgress
              state={data.request.state}
              subStatus={data.request.sub_status}
              outcome={data.request.resolution_status}
              audience="dpo"
            />
            <Card className="border-0 shadow-sm ring-1 ring-border/80">
              <CardContent className="flex flex-col justify-between gap-5 pt-6 sm:flex-row sm:items-start">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
                    DPO CASE WORKSPACE
                  </p>
                  <h1 className="mt-2 text-3xl font-semibold tracking-tight">
                    DSR-{data.request.id} · {data.request.request_type_label ?? data.request.request_type}
                  </h1>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Submitted {formatDate(data.request.submitted_at)}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={statusVariant(data.request.state)}>
                    {data.request.state}
                  </Badge>
                  {data.request.sub_status && (
                    <Badge variant="outline">{data.request.sub_status}</Badge>
                  )}
                  {data.request.case_stage && (
                    <Badge variant="secondary">
                      Stage: {data.request.case_stage.replaceAll("_", " ")}
                    </Badge>
                  )}
                  {data.request.escalation_active && (
                    <Badge variant="destructive">Escalated</Badge>
                  )}
                  {data.request.waiting_for_subject_information && (
                    <Badge variant="outline">Waiting for patient information</Badge>
                  )}
                  {overdue && <Badge variant="destructive">Overdue</Badge>}
                </div>
              </CardContent>
            </Card>

            <div className={`grid gap-5 ${role === "dpo" ? "xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,1fr)]" : ""}`}>
              <Card className="border-0 shadow-sm ring-1 ring-border/80">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-lg">
                    Request overview
                  </CardTitle>
                  <CardDescription>
                    Mock request details only. Do not enter or include real patient or clinical information.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <dl className="grid gap-4 sm:grid-cols-2">
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Intake jurisdiction
                      </dt>
                      <dd className="mt-1 text-sm font-medium">
                        {data.request.jurisdiction_code ?? "KE"} · policy snapshot
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Department
                      </dt>
                      <dd className="mt-1 text-sm font-medium">
                        {data.request.department || "Not specified"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Resolution
                      </dt>
                      <dd className="mt-1 text-sm font-medium">
                        {data.request.resolution_status}
                      </dd>
                    </div>
                    <div className="sm:col-span-2">
                      <dt className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        <CalendarClock className="size-3.5" />
                        Internal target
                      </dt>
                      <dd className={`mt-1 text-sm font-medium ${overdue ? "text-rose-700" : ""}`}>
                        {formatDate(data.request.response_due_at)}
                        {data.request.state !== "Completed"
                          ? ` · ${formatSlaDue(data.request.response_due_at, currentTime, data.request.time_remaining_seconds)}`
                          : " · Target date"}
                      </dd>
                    </div>
                    <div className="sm:col-span-2">
                      <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Legal deadline
                      </dt>
                      <dd className="mt-1 text-sm font-medium">
                        {data.request.legal_deadline_at
                          ? formatDate(data.request.legal_deadline_at)
                          : "Not configured"}
                      </dd>
                    </div>
                    {data.request.duplicate_of && (
                      <div className="sm:col-span-2">
                        <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          Original request
                        </dt>
                        <dd className="mt-1 text-sm font-medium">
                          DSR-{data.request.duplicate_of}
                        </dd>
                      </div>
                    )}
                    {data.request.reconsideration_of && (
                      <div className="sm:col-span-2">
                        <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          Reconsideration of
                        </dt>
                        <dd className="mt-1 text-sm font-medium">
                          DSR-{data.request.reconsideration_of}
                        </dd>
                      </div>
                    )}
                  </dl>
                  {data.request.independent_review_required && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                      <p className="font-semibold">Independent review required</p>
                      <p className="mt-1">{data.request.independent_review_reason}</p>
                    </div>
                  )}
                  <div className="border-t pt-5">
                    <h2 className="text-sm font-semibold">Description</h2>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
                      {data.request.description}
                    </p>
                  </div>
                </CardContent>
              </Card>

              <div className="space-y-5">
                {role === "dpo" && <Card className="border-0 shadow-sm ring-1 ring-border/80">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                      <UserRound className="size-4 text-primary" />
                      Assignment
                    </CardTitle>
                    <CardDescription>
                      Assign this request to a mock DPO or staff ID.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <form
                      onSubmit={(event) =>
                        void submitMutation(
                          event,
                          "PATCH",
                          { assigned_to: assignedTo.trim() },
                          "Request assignment saved.",
                        )
                      }
                      className="space-y-3"
                    >
                      <label htmlFor="assigned-to" className="text-sm font-medium">
                        Reviewer ID
                      </label>
                      <Input
                        id="assigned-to"
                        value={assignedTo}
                        onChange={(event) => setAssignedTo(event.target.value)}
                        maxLength={128}
                        pattern="[A-Za-z0-9_.:-]{0,128}"
                        placeholder="e.g. dpo-reviewer-1"
                        disabled={saving}
                      />
                      <p className="text-xs text-muted-foreground">
                        Leave blank to unassign. Allowed characters: letters, numbers, _, ., :, -
                      </p>
                      <Button
                        type="submit"
                        variant="outline"
                        disabled={saving || assignedTo === data.request.assigned_to}
                        className="gap-2"
                      >
                        {saving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
                        Save assignment
                      </Button>
                    </form>
                  </CardContent>
                </Card>}

                <Card className="border-0 shadow-sm ring-1 ring-border/80">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-lg">
                      <Workflow className="size-4 text-primary" />
                      Workflow action
                    </CardTitle>
                    <CardDescription>
                      Only actions available for the current request status are shown.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    {data.available_actions.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No workflow actions are available for this request.
                      </p>
                    ) : (
                      <form
                        onSubmit={(event) =>
                          void submitMutation(
                            event,
                            "POST",
                            {
                              action,
                              reason: reason.trim(),
                              ...(action === "approve_and_close"
                                ? { resolution_status: resolutionStatus }
                                : {}),
                              ...(action === "request_legal_review"
                                ? {
                                    proposed_outcome: proposedOutcome,
                                    assigned_reviewer: assignedReviewer.trim(),
                                  }
                                : {}),
                              ...(action === "close_as_duplicate"
                                || action === "mark_for_duplicate"
                                ? { duplicate_of: Number(duplicateOf) }
                                : {}),
                            },
                            `Request status updated${selectedAction ? `: ${selectedAction.label}` : ""}.`,
                          )
                        }
                        className="space-y-3"
                      >
                        <label htmlFor="workflow-action" className="text-sm font-medium">
                          Available action
                        </label>
                        <select
                          id="workflow-action"
                          value={action}
                          onChange={(event) => setAction(event.target.value)}
                          disabled={saving}
                          className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                        >
                          {data.available_actions.map((item) => (
                            <option key={item.action} value={item.action}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                        {action === "approve_and_close" && (
                          <>
                            <label htmlFor="resolution-status" className="text-sm font-medium">
                              Resolution outcome
                            </label>
                            <select
                              id="resolution-status"
                              value={resolutionStatus}
                              onChange={(event) => setResolutionStatus(event.target.value)}
                              disabled={saving}
                              className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                            >
                              {resolutions.map(([value, label]) => {
                                const requiresReview =
                                  data.request.independent_review_required ||
                                  ["PARTIALLY_FULFILLED", "REJECTED", "RETAINED"].includes(value);
                                return !data.request.legal_review_completed &&
                                  requiresReview
                                  ? null
                                  : (
                                    <option key={value} value={value}>
                                      {label}
                                    </option>
                                  );
                              })}
                            </select>
                            {!data.request.legal_review_completed && (
                              <p className="text-xs text-muted-foreground">
                                Refusal and partial outcomes are available after Legal / Management Review.
                              </p>
                            )}
                          </>
                        )}
                        {action === "request_legal_review" && (
                          <>
                            <label htmlFor="proposed-outcome" className="text-sm font-medium">
                              Outcome for independent review
                            </label>
                            <select
                              id="proposed-outcome"
                              value={proposedOutcome}
                              onChange={(event) => setProposedOutcome(event.target.value)}
                              disabled={saving}
                              className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                            >
                              {reviewOutcomes.map(([value, label]) => (
                                <option key={value} value={value}>{label}</option>
                              ))}
                            </select>
                            <p className="text-xs text-muted-foreground">
                              Review is bound to the latest unpublished response version and current case evidence.
                            </p>
                            <label htmlFor="assigned-reviewer" className="text-sm font-medium">
                              Independent Reviewer ID
                            </label>
                            <Input
                              id="assigned-reviewer"
                              required
                              value={assignedReviewer}
                              onChange={(event) => setAssignedReviewer(event.target.value)}
                              maxLength={128}
                              pattern="[A-Za-z0-9_.:-]{1,128}"
                              placeholder="e.g. mock-independent-reviewer"
                              disabled={saving}
                            />
                          </>
                        )}
                        {(action === "close_as_duplicate" || action === "mark_for_duplicate") && (
                          <div className="space-y-2">
                            <label htmlFor="duplicate-of" className="text-sm font-medium">
                              Canonical original request ID
                            </label>
                            <Input
                              id="duplicate-of"
                              type="number"
                              min={1}
                              required
                              value={duplicateOf}
                              onChange={(event) => setDuplicateOf(event.target.value)}
                              placeholder="Enter the matching request ID"
                              disabled={saving}
                            />
                            <p className="text-xs text-muted-foreground">
                              The original must be a canonical case for the same requester and request type. Mark the case first, then publish its duplicate outcome before closing.
                            </p>
                          </div>
                        )}
                        <label htmlFor="workflow-reason" className="text-sm font-medium">
                          Reason {requiresReason ? "(required)" : "(optional)"}
                        </label>
                        <Textarea
                          id="workflow-reason"
                          value={reason}
                          onChange={(event) => setReason(event.target.value)}
                          maxLength={2000}
                          required={requiresReason}
                          placeholder="Add a brief reason for this action"
                          disabled={saving}
                        />
                        <Button
                          type="submit"
                          disabled={
                            saving ||
                            !action ||
                            (action === "request_legal_review" && !assignedReviewer.trim()) ||
                            (["close_as_duplicate", "mark_for_duplicate"].includes(action) && !duplicateOf)
                          }
                          className="gap-2"
                        >
                          {saving ? <LoaderCircle className="size-4 animate-spin" /> : <Workflow className="size-4" />}
                          Apply action
                        </Button>
                      </form>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>

            <Card className="border-0 shadow-sm ring-1 ring-border/80">
              <CardHeader>
                <CardTitle className="text-lg">Independent review history</CardTitle>
                <CardDescription>
                  Review decisions apply only to the exact response version and case evidence listed.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {data.legal_reviews.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No independent review has been requested.</p>
                ) : (
                  <ol className="space-y-4">
                    {data.legal_reviews.map((review) => (
                      <li key={review.id} className="rounded-lg border p-4 text-sm">
                        <p className="font-medium">
                          Version {review.response_version} · {review.proposed_outcome}
                        </p>
                        <p className="mt-1 text-muted-foreground">
                          Requested by {review.requested_by} on {formatDate(review.requested_at)}: {review.request_reason}
                        </p>
                        {review.decision ? (
                          <p className="mt-2">
                            {review.decision.outcome} by {review.decision.reviewer_id} on {formatDate(review.decision.decided_at)}: {review.decision.rationale}
                          </p>
                        ) : (
                          <p className="mt-2 font-medium text-amber-800">Awaiting independent review.</p>
                        )}
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>

            {data.review_material && (
              <Card className="border-primary/30 shadow-sm ring-1 ring-primary/15">
                <CardHeader>
                  <CardTitle className="text-lg">Assigned review material</CardTitle>
                  <CardDescription>
                    Review the exact version and snapshot assigned to you. Case changes invalidate approval.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm font-medium">
                    Response version {data.review_material.response_version}
                  </p>
                  <pre className="whitespace-pre-wrap rounded-lg bg-muted p-4 text-sm leading-6">
                    {data.review_material.response_content}
                  </pre>
                  <details>
                    <summary className="cursor-pointer text-sm font-medium">
                      Evidence snapshot
                    </summary>
                    <pre className="mt-3 overflow-x-auto rounded-lg bg-muted p-4 text-xs">
                      {JSON.stringify(data.review_material.case_snapshot, null, 2)}
                    </pre>
                  </details>
                </CardContent>
              </Card>
            )}

            <Card className="border-0 shadow-sm ring-1 ring-border/80">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <MessageSquareText className="size-4 text-primary" />
                  Patient messages
                </CardTitle>
                <CardDescription>
                  Messages appear in both portals. They are stored in this prototype; no email or SMS is sent.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                {data.communications.length === 0 ? (
                  <p className="rounded-lg bg-muted/50 p-4 text-sm text-muted-foreground">
                    No messages have been exchanged on this request yet.
                  </p>
                ) : (
                  <ol className="space-y-3">
                    {data.communications.map((communication) => {
                      const fromDpo = communication.direction === "DPO to patient";
                      return (
                        <li
                          key={communication.id}
                          className={`rounded-xl border p-4 ${
                            fromDpo ? "border-sky-200 bg-sky-50/70" : "bg-white"
                          }`}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-sm font-semibold">
                              {fromDpo ? "You · DPO" : "Patient"}
                            </p>
                            <time
                              dateTime={communication.created_at}
                              className="text-xs text-muted-foreground"
                            >
                              {formatDate(communication.created_at)}
                            </time>
                          </div>
                          <p className="mt-2 whitespace-pre-wrap text-sm leading-6">
                            {communication.message}
                          </p>
                        </li>
                      );
                    })}
                  </ol>
                )}

                <form onSubmit={sendPatientMessage} className="space-y-3 border-t pt-5">
                  <label htmlFor="patient-message" className="text-sm font-semibold">
                    Send a message to the patient
                  </label>
                  <p className="text-xs leading-5 text-muted-foreground">
                    If you need information to continue, also use the workflow action to move the case to “Waiting for Subject Information”.
                  </p>
                  <Textarea
                    id="patient-message"
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                    maxLength={5000}
                    required
                    placeholder="Write a question or update for the patient"
                    disabled={sendingMessage}
                  />
                  <Button
                    type="submit"
                    disabled={sendingMessage || !message.trim()}
                    className="gap-2"
                  >
                    {sendingMessage ? (
                      <LoaderCircle className="size-4 animate-spin" />
                    ) : (
                      <Send className="size-4" />
                    )}
                    {sendingMessage ? "Sending…" : "Send message"}
                  </Button>
                </form>
              </CardContent>
            </Card>

            {role === "dpo" && (
              <>
                {data.request.case_stage === "INTAKE" && (
                  <Card className="border-0 shadow-sm ring-1 ring-border/80">
                    <CardHeader>
                      <CardTitle className="text-lg">Intake jurisdiction</CardTitle>
                      <CardDescription>
                        Select the applicable jurisdiction during intake. The request will retain the exact
                        deadline-policy version and calculated dates selected here.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <form onSubmit={(event) => void updateJurisdiction(event)} className="space-y-3">
                        <label htmlFor="case-jurisdiction" className="text-sm font-medium">
                          Jurisdiction code
                        </label>
                        <Input
                          id="case-jurisdiction"
                          value={jurisdictionCode}
                          maxLength={8}
                          pattern="[A-Z]{2,8}"
                          required
                          onChange={(event) => setJurisdictionCode(event.target.value.toUpperCase())}
                        />
                        <Textarea
                          value={jurisdictionReason}
                          onChange={(event) => setJurisdictionReason(event.target.value)}
                          placeholder="Reason for assigning this jurisdiction"
                          required
                          maxLength={2000}
                        />
                        <Button type="submit" disabled={saving || !jurisdictionReason.trim()}>
                          Save intake jurisdiction
                        </Button>
                      </form>
                    </CardContent>
                  </Card>
                )}

                <Card className="border-0 shadow-sm ring-1 ring-border/80">
                  <CardHeader>
                    <CardTitle className="text-lg">Parallel work items</CardTitle>
                    <CardDescription>
                      Searches, privacy review, legal/management input, and response work can be assigned
                      independently and run in any order. Required items gate response finalization.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    {data.work_items.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No parallel work items have been assigned.</p>
                    ) : (
                      <ul className="space-y-3">
                        {data.work_items.map((item) => (
                          <li key={item.id} className="rounded-lg border p-4">
                            <p className="font-medium">{item.title}</p>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {item.task_type.replaceAll("_", " ")} · {item.status}
                              {item.assigned_to ? ` · assigned to ${item.assigned_to}` : " · unassigned"}
                              {item.required_for_completion ? " · required before response" : ""}
                              {item.due_at ? ` · due ${formatDate(item.due_at)}` : ""}
                            </p>
                            {item.details && <p className="mt-2 whitespace-pre-wrap text-sm">{item.details}</p>}
                            {item.completion_summary && (
                              <p className="mt-2 text-sm text-muted-foreground">
                                Outcome: {item.completion_summary}
                              </p>
                            )}
                            <form
                              className="mt-3 grid gap-3 sm:grid-cols-[200px_1fr_auto]"
                              onSubmit={(event) => void updateWorkItem(event, item.id)}
                            >
                              <select
                                name="status"
                                defaultValue={item.status.toUpperCase().replaceAll(" ", "_")}
                                className="h-10 rounded-lg border border-input bg-white px-3 text-sm"
                              >
                                {workStatuses.map(([code, label]) => (
                                  <option key={code} value={code}>{label}</option>
                                ))}
                              </select>
                              <Input
                                name="completion_summary"
                                defaultValue={item.completion_summary}
                                placeholder="Completion summary (required to complete)"
                                maxLength={4000}
                              />
                              <Button type="submit" variant="outline" disabled={taskSaving}>
                                Update
                              </Button>
                            </form>
                          </li>
                        ))}
                      </ul>
                    )}
                    <form onSubmit={(event) => void createWorkItem(event)} className="grid gap-3 border-t pt-5 sm:grid-cols-2">
                      <Input
                        value={taskTitle}
                        onChange={(event) => setTaskTitle(event.target.value)}
                        placeholder="Work item title"
                        maxLength={160}
                        required
                      />
                      <select
                        value={taskType}
                        onChange={(event) => setTaskType(event.target.value as typeof taskType)}
                        className="h-10 rounded-lg border border-input bg-white px-3 text-sm"
                      >
                        {workTypes.map(([code, label]) => (
                          <option key={code} value={code}>{label}</option>
                        ))}
                      </select>
                      <Input
                        value={taskAssignee}
                        onChange={(event) => setTaskAssignee(event.target.value)}
                        placeholder="Assigned Department Contributor ID"
                        maxLength={128}
                        pattern="[A-Za-z0-9_.:-]{0,128}"
                      />
                      <Input
                        type="datetime-local"
                        value={taskDueAt}
                        onChange={(event) => setTaskDueAt(event.target.value)}
                        aria-label="Work item due date"
                      />
                      <Textarea
                        value={taskDetails}
                        onChange={(event) => setTaskDetails(event.target.value)}
                        placeholder="Instructions and expected evidence summary"
                        maxLength={4000}
                        className="sm:col-span-2"
                      />
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={taskRequired}
                          onChange={(event) => setTaskRequired(event.target.checked)}
                        />
                        Required before response finalization
                      </label>
                      <Button type="submit" disabled={taskSaving || !taskTitle.trim()}>
                        {taskSaving ? "Saving…" : "Add parallel work item"}
                      </Button>
                    </form>
                  </CardContent>
                </Card>

                <Card className="border-0 shadow-sm ring-1 ring-border/80">
                  <CardHeader>
                    <CardTitle className="text-lg">Intake exceptions and deadline actions</CardTitle>
                    <CardDescription>
                      Record the rationale and patient notice. Deadline changes are permitted only when
                      the case&apos;s versioned jurisdiction policy authorizes them.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {data.exceptions.length > 0 && (
                      <ul className="space-y-2">
                        {data.exceptions.map((item) => (
                          <li key={item.id} className="rounded-lg border p-3 text-sm">
                            <p className="font-medium">{item.exception_type}</p>
                            <p className="mt-1">{item.reason}</p>
                            <p className="mt-1 text-xs text-muted-foreground">
                              Recorded by {item.actor_id} · {formatDate(item.created_at)}
                              {item.deadline_effect_seconds
                                ? ` · deadline effect ${Math.round(item.deadline_effect_seconds / 86400)} days`
                                : ""}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                    <form onSubmit={(event) => void createException(event)} className="space-y-3">
                      <select
                        value={exceptionType}
                        onChange={(event) => setExceptionType(event.target.value)}
                        className="h-10 w-full rounded-lg border border-input bg-white px-3 text-sm"
                      >
                        <option value="CLARIFICATION">Clarification required</option>
                        <option value="OUT_OF_SCOPE">Out of scope</option>
                        <option value="UNABLE_TO_PROCESS">Unable to process</option>
                        <option value="EXTENSION">Authorized deadline extension</option>
                        <option value="DEADLINE_PAUSE">Authorized deadline pause</option>
                      </select>
                      <Textarea
                        value={exceptionReason}
                        onChange={(event) => setExceptionReason(event.target.value)}
                        placeholder="Reason and applicable policy basis"
                        required
                        maxLength={4000}
                      />
                      {(exceptionType === "EXTENSION" || exceptionType === "DEADLINE_PAUSE") && (
                        <Input
                          type="number"
                          min={1}
                          max={3650}
                          value={deadlineEffectDays}
                          onChange={(event) => setDeadlineEffectDays(event.target.value)}
                          placeholder="Authorized deadline adjustment in days"
                          required
                        />
                      )}
                      {exceptionType === "DEADLINE_PAUSE" && (
                        <Input
                          value={exceptionPolicyReason}
                          onChange={(event) => setExceptionPolicyReason(event.target.value)}
                          placeholder="Exact reason authorized by this policy"
                          required
                          maxLength={120}
                        />
                      )}
                      <Textarea
                        value={patientNotification}
                        onChange={(event) => setPatientNotification(event.target.value)}
                        placeholder="Patient-facing notice (required for intake/clock exceptions)"
                        required
                        maxLength={4000}
                      />
                      <Button
                        type="submit"
                        disabled={
                          exceptionSaving ||
                          !exceptionReason.trim() ||
                          !patientNotification.trim() ||
                          (exceptionType === "DEADLINE_PAUSE" && !exceptionPolicyReason.trim()) ||
                          ((exceptionType === "EXTENSION" || exceptionType === "DEADLINE_PAUSE") &&
                            !deadlineEffectDays)
                        }
                      >
                        {exceptionSaving ? "Saving…" : "Record exception"}
                      </Button>
                    </form>
                  </CardContent>
                </Card>
              </>
            )}

            {role === "dpo" && <Card className="border-0 shadow-sm ring-1 ring-border/80">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <History className="size-4 text-primary" />
                  Activity history
                </CardTitle>
                <CardDescription>
                  Workflow changes and recorded case activity, newest first.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {data.activity.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No activity has been recorded for this request yet.
                  </p>
                ) : (
                  <ol className="divide-y">
                    {data.activity.map((item, index) => (
                      <li
                        key={`${item.kind}-${item.timestamp}-${index}`}
                        className="grid gap-1 py-4 first:pt-0 sm:grid-cols-[1fr_auto] sm:gap-4"
                      >
                        <div>
                          <p className="text-sm font-medium">{item.summary}</p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {item.kind === "transition" ? "Workflow change" : "Case activity"} · {item.actor_id}
                          </p>
                        </div>
                        <time
                          dateTime={item.timestamp}
                          className="text-xs text-muted-foreground sm:text-right"
                        >
                          {formatDate(item.timestamp)}
                        </time>
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>}
          </>
        )}
      </div>
    </AppShell>
  );
}
