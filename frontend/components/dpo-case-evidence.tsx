"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bot, CheckCircle2, FileText, Gavel, LoaderCircle, SearchCheck, Send } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  DSRCaseDetailResponse,
  DSRResponseDraft,
  DSRSourceSearch,
  IntegrationJob,
  IntegrationJobsResponse,
} from "@/lib/types";

const SOURCE_SYSTEMS = [
  ["PARAS", "PARAS (mock source)"],
  ["CIMS", "CIMS (mock source)"],
  ["ERPS", "ERPS (mock source)"],
  ["DYNAMICS", "Microsoft Dynamics 365 (mock source)"],
  ["OTHER", "Other mock source"],
] as const;
const SEARCH_STATUSES = [
  ["PENDING", "Pending"],
  ["COMPLETE", "Complete"],
  ["BLOCKED", "Blocked"],
] as const;
const DECISION_OUTCOMES = [
  ["FULFILLED", "Fulfilled"],
  ["PARTIALLY_FULFILLED", "Partially fulfilled"],
  ["RETAINED", "Retained with rationale"],
  ["REJECTED", "Rejected"],
  ["OUT_OF_SCOPE", "Out of scope"],
  ["UNABLE_TO_PROCESS", "Unable to process"],
  ["DUPLICATE", "Duplicate"],
  ["FURTHER_REVIEW", "Further review required"],
] as const;

const selectClass =
  "h-10 w-full rounded-lg border border-input bg-white px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-KE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Nairobi",
  }).format(new Date(value));
}

function statusCode(label: string): string {
  return SEARCH_STATUSES.find(([, text]) => text === label)?.[0] ?? "PENDING";
}

function sourceCode(label: string): string | undefined {
  return SOURCE_SYSTEMS.find(([, text]) => text === label)?.[0];
}

function searchBadge(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "Complete") return "secondary";
  if (status === "Blocked") return "destructive";
  return "outline";
}

function jobLine(job: IntegrationJob): string {
  if (job.status === "PENDING" && job.attempts > 0) {
    return `Automated search retrying: attempt ${job.attempts} of ${job.max_attempts} failed, next try ${formatDate(job.next_attempt_at)}.`;
  }
  if (job.status === "PENDING") return "Automated search queued.";
  if (job.status === "RUNNING") return "Automated search running…";
  if (job.status === "FAILED") {
    return `Automated search failed after ${job.attempts} attempt(s). Retry it from Integrations, or search by hand.`;
  }
  return "Completed by automated search.";
}

async function send(url: string, method: "POST" | "PATCH", payload?: object): Promise<void> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "The change could not be saved.");
}

/**
 * Evidence and response panels for the DPO case page: source searches,
 * decisions, and the draft → finalize → publish steps. Every rule is enforced
 * by the API; the text here explains the rule so a blocked step is not a surprise.
 */
export function DpoCaseEvidence({
  id,
  data,
  onChanged,
}: {
  id: number;
  data: DSRCaseDetailResponse;
  onChanged: () => Promise<unknown>;
}) {
  const searches = data.source_searches ?? [];
  const decisions = [...(data.decisions ?? [])].reverse();
  const versions = data.response_versions ?? [];
  const latestVersion = versions[0];
  const completed = data.request.state === "Completed";
  const readiness = data.response_readiness ?? null;

  const [busy, setBusy] = useState<string | null>(null);
  const usedSystems = new Set(searches.map((item) => sourceCode(item.source_system)));
  const freeSystems = SOURCE_SYSTEMS.filter(([code]) => !usedSystems.has(code));
  const jobs = data.integration_jobs ?? [];
  const latestJobFor = (searchId: number) => jobs.find((job) => job.source_search === searchId);
  const automatable = SOURCE_SYSTEMS.filter(([code]) => {
    if (!(data.automated_source_systems ?? []).includes(code)) return false;
    const existing = searches.find((item) => sourceCode(item.source_system) === code);
    if (!existing) return true;
    if (existing.status === "Complete") return false;
    const job = latestJobFor(existing.id);
    return !job || job.status === "FAILED" || job.status === "SUCCEEDED";
  });
  const [autoSystem, setAutoSystem] = useState<string>("");

  // While a job is queued or running, check the (small) jobs list every few
  // seconds and reload the case once when something finishes. Polling only
  // the jobs list keeps unsaved edits elsewhere on the page intact.
  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  });
  const activeJobIds = jobs
    .filter((job) => job.status === "PENDING" || job.status === "RUNNING")
    .map((job) => job.id)
    .join(",");
  useEffect(() => {
    if (!activeJobIds) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`/api/integration-jobs?request=${id}`, { cache: "no-store" });
        if (!response.ok) return;
        const body = (await response.json()) as IntegrationJobsResponse;
        const stillActive = body.jobs
          .filter((job) => job.status === "PENDING" || job.status === "RUNNING")
          .map((job) => job.id)
          .join(",");
        if (!stopped && stillActive !== activeJobIds) await onChangedRef.current();
      } catch {
        // Try again on the next tick.
      }
    }, 4000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeJobIds, id]);
  const [newSystem, setNewSystem] = useState<string>("");
  const [newStatus, setNewStatus] = useState("PENDING");
  const [newSummary, setNewSummary] = useState("");
  const [newEvidence, setNewEvidence] = useState("");
  const [decisionCategory, setDecisionCategory] = useState("overall");
  const [decisionOutcome, setDecisionOutcome] = useState<string>("FULFILLED");
  const [decisionRationale, setDecisionRationale] = useState("");
  const [responseText, setResponseText] = useState("");
  const [draftNotice, setDraftNotice] = useState("");

  async function run(key: string, task: () => Promise<void>, success: string) {
    setBusy(key);
    try {
      await task();
      toast.success(success);
      await onChanged();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The change could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  function addSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const system = newSystem || freeSystems[0]?.[0];
    if (!system) return;
    void run(
      "search-new",
      async () => {
        await send(`/api/requests/${id}/source-searches`, "POST", {
          source_system: system,
          status: newStatus,
          summary: newSummary.trim(),
          evidence_reference: newEvidence.trim(),
        });
        setNewSystem("");
        setNewStatus("PENDING");
        setNewSummary("");
        setNewEvidence("");
      },
      "Source search recorded.",
    );
  }

  function runAutomatedSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const system = autoSystem || automatable[0]?.[0];
    if (!system) return;
    void run(
      "search-auto",
      async () => {
        await send(`/api/requests/${id}/source-searches/automated`, "POST", {
          source_system: system,
        });
        setAutoSystem("");
      },
      "Automated search queued. The result appears here when it finishes.",
    );
  }

  function updateSearch(event: FormEvent<HTMLFormElement>, search: DSRSourceSearch) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void run(
      `search-${search.id}`,
      () =>
        send(`/api/requests/${id}/source-searches/${search.id}`, "PATCH", {
          status: String(form.get("status")),
          summary: String(form.get("summary") ?? "").trim(),
          evidence_reference: String(form.get("evidence_reference") ?? "").trim(),
        }),
      "Source search updated.",
    );
  }

  function recordDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(
      "decision",
      async () => {
        await send(`/api/requests/${id}/decisions`, "POST", {
          category: decisionCategory.trim() || "overall",
          outcome: decisionOutcome,
          rationale: decisionRationale.trim(),
        });
        setDecisionRationale("");
      },
      "Decision recorded.",
    );
  }

  async function loadDraft() {
    setBusy("draft");
    try {
      const response = await fetch(`/api/requests/${id}/response-draft`, { cache: "no-store" });
      const body = (await response.json()) as DSRResponseDraft | { detail?: string };
      if (!response.ok || !("draft_text" in body)) {
        throw new Error("detail" in body && body.detail ? body.detail : "Could not build a draft.");
      }
      setResponseText(body.draft_text);
      setDraftNotice(body.notice);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not build a draft.");
    } finally {
      setBusy(null);
    }
  }

  function finalize(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = (latestVersion?.version ?? 0) + 1;
    if (!window.confirm(`Finalize this text as version ${next}? Finalized versions cannot be edited.`)) {
      return;
    }
    void run(
      "finalize",
      async () => {
        await send(`/api/requests/${id}/response-versions`, "POST", {
          content: responseText.trim(),
        });
        setResponseText("");
        setDraftNotice("");
      },
      `Response version ${next} finalized. The patient cannot see it until you publish it.`,
    );
  }

  function publish(versionId: number, version: number) {
    if (
      !window.confirm(
        `Publish version ${version} to the patient portal? The patient will be able to read it and can no longer withdraw. No email or SMS is sent.`,
      )
    ) {
      return;
    }
    void run(
      `publish-${versionId}`,
      () => send(`/api/requests/${id}/response-versions/${versionId}/publish`, "POST"),
      `Version ${version} published to the patient portal.`,
    );
  }

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      {/* Source searches */}
      <Card className="border-0 shadow-sm ring-1 ring-border/80">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <SearchCheck className="size-4 text-primary" />
            Source searches
          </CardTitle>
          <CardDescription>
            One line per system. Search a connected system automatically, or search by hand and
            record the result. To approve privacy review, at least one search must exist and every search must be
            Complete.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {searches.length === 0 ? (
            <p className="rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">
              No searches recorded yet.
            </p>
          ) : (
            <ul className="space-y-3">
              {searches.map((search) => (
                <li key={search.id} className="rounded-lg border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-medium">{search.source_system}</p>
                    <Badge variant={searchBadge(search.status)}>{search.status}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Updated by {search.updated_by} · {formatDate(search.updated_at)}
                  </p>
                  {(() => {
                    const job = latestJobFor(search.id);
                    if (!job) return null;
                    return (
                      <p
                        className={`mt-2 flex items-start gap-1.5 text-xs ${
                          job.status === "FAILED" ? "text-rose-700" : "text-muted-foreground"
                        }`}
                      >
                        <Bot className="mt-0.5 size-3.5 shrink-0" />
                        <span>
                          {jobLine(job)}
                          {job.last_error && job.status !== "SUCCEEDED" ? ` Last error: ${job.last_error}` : ""}
                        </span>
                      </p>
                    );
                  })()}
                  {!completed && (
                    <form
                      className="mt-3 grid gap-2 sm:grid-cols-[140px_1fr]"
                      onSubmit={(event) => updateSearch(event, search)}
                    >
                      <select
                        name="status"
                        aria-label={`Status for ${search.source_system}`}
                        defaultValue={statusCode(search.status)}
                        className={selectClass}
                      >
                        {SEARCH_STATUSES.map(([code, label]) => (
                          <option key={code} value={code}>{label}</option>
                        ))}
                      </select>
                      <Input
                        name="evidence_reference"
                        aria-label={`Evidence reference for ${search.source_system}`}
                        defaultValue={search.evidence_reference}
                        placeholder="Evidence reference, e.g. ticket or export ID"
                        maxLength={256}
                      />
                      <Textarea
                        name="summary"
                        aria-label={`Summary for ${search.source_system}`}
                        defaultValue={search.summary}
                        placeholder="What was found (required for Complete or Blocked)"
                        className="sm:col-span-2"
                        maxLength={4000}
                      />
                      <Button
                        type="submit"
                        variant="outline"
                        disabled={busy !== null}
                        className="sm:col-span-2 sm:justify-self-start"
                      >
                        {busy === `search-${search.id}` ? "Saving…" : "Update search"}
                      </Button>
                    </form>
                  )}
                  {completed && (
                    <p className="mt-2 text-sm">
                      {search.summary || "No summary."}
                      {search.evidence_reference ? ` · ${search.evidence_reference}` : ""}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
          {!completed && automatable.length > 0 && (
            <form
              onSubmit={runAutomatedSearch}
              className="grid gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3 sm:grid-cols-[1fr_auto] sm:items-end"
            >
              <label className="space-y-1 text-sm font-medium">
                Search a connected system automatically
                <select
                  id="auto-search-system"
                  value={autoSystem || automatable[0][0]}
                  onChange={(event) => setAutoSystem(event.target.value)}
                  className={selectClass}
                >
                  {automatable.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </label>
              <Button type="submit" disabled={busy !== null} className="gap-2">
                <Bot className="size-4" />
                {busy === "search-auto" ? "Queuing…" : "Search automatically"}
              </Button>
              <p className="text-xs text-muted-foreground sm:col-span-2">
                The connector returns a record count and a reference only, never the records.
                Failed searches are retried automatically, then marked Blocked.
              </p>
            </form>
          )}
          {!completed && freeSystems.length > 0 && (
            <form onSubmit={addSearch} className="grid gap-2 border-t pt-4 sm:grid-cols-2">
              <label className="space-y-1 text-sm font-medium">
                System
                <select
                  id="new-search-system"
                  value={newSystem || freeSystems[0][0]}
                  onChange={(event) => setNewSystem(event.target.value)}
                  className={selectClass}
                >
                  {freeSystems.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </label>
              <label className="space-y-1 text-sm font-medium">
                Status
                <select
                  id="new-search-status"
                  value={newStatus}
                  onChange={(event) => setNewStatus(event.target.value)}
                  className={selectClass}
                >
                  {SEARCH_STATUSES.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </label>
              <Textarea
                id="new-search-summary"
                value={newSummary}
                onChange={(event) => setNewSummary(event.target.value)}
                placeholder="Summary of what was found (required unless Pending)"
                className="sm:col-span-2"
                maxLength={4000}
              />
              <Input
                id="new-search-evidence"
                value={newEvidence}
                onChange={(event) => setNewEvidence(event.target.value)}
                placeholder="Evidence reference (optional)"
                maxLength={256}
              />
              <Button
                type="submit"
                disabled={busy !== null || (newStatus !== "PENDING" && !newSummary.trim())}
              >
                {busy === "search-new" ? "Saving…" : "Record search"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>

      {/* Decisions */}
      <Card className="border-0 shadow-sm ring-1 ring-border/80">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Gavel className="size-4 text-primary" />
            Decisions
          </CardTitle>
          <CardDescription>
            The latest “overall” decision sets the outcome used to publish and close. Any outcome
            except “Further review required” needs every source search Complete first. Decisions
            can&apos;t be edited; record a new one instead.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {decisions.length === 0 ? (
            <p className="rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">
              No decisions recorded yet.
            </p>
          ) : (
            <ol className="space-y-3">
              {decisions.map((decision, index) => (
                <li key={decision.id} className="rounded-lg border p-4 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{decision.outcome}</p>
                    <Badge variant="outline">{decision.category}</Badge>
                    {index === 0 && <Badge variant="secondary">Latest</Badge>}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap">{decision.rationale}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {decision.recorded_by} · {formatDate(decision.created_at)}
                  </p>
                </li>
              ))}
            </ol>
          )}
          {!completed && (
            <form onSubmit={recordDecision} className="grid gap-2 border-t pt-4 sm:grid-cols-2">
              <label className="space-y-1 text-sm font-medium">
                Outcome
                <select
                  id="decision-outcome"
                  value={decisionOutcome}
                  onChange={(event) => setDecisionOutcome(event.target.value)}
                  className={selectClass}
                >
                  {DECISION_OUTCOMES.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </label>
              <label className="space-y-1 text-sm font-medium">
                Category
                <Input
                  id="decision-category"
                  value={decisionCategory}
                  onChange={(event) => setDecisionCategory(event.target.value)}
                  maxLength={80}
                />
              </label>
              {["PARTIALLY_FULFILLED", "REJECTED", "RETAINED"].includes(decisionOutcome) && (
                <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950 sm:col-span-2">
                  This outcome needs an approved independent review before it can be published.
                </p>
              )}
              <Textarea
                id="decision-rationale"
                value={decisionRationale}
                onChange={(event) => setDecisionRationale(event.target.value)}
                placeholder="Rationale"
                required
                className="sm:col-span-2"
                maxLength={4000}
              />
              <Button
                type="submit"
                disabled={busy !== null || !decisionRationale.trim()}
                className="sm:col-span-2 sm:justify-self-start"
              >
                {busy === "decision" ? "Saving…" : "Record decision"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>

      {/* Response */}
      <Card className="border-0 shadow-sm ring-1 ring-border/80 xl:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <FileText className="size-4 text-primary" />
            Response to the patient
          </CardTitle>
          <CardDescription>
            Three separate steps: write the text, finalize it as a numbered version (it can&apos;t
            be edited after that), then publish it to the patient portal. Publishing sends no
            email or SMS.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {!completed && (
            <div
              className={`rounded-lg border p-3 text-sm ${
                readiness
                  ? "border-amber-200 bg-amber-50 text-amber-950"
                  : "border-emerald-200 bg-emerald-50 text-emerald-950"
              }`}
              role="status"
            >
              {readiness ? (
                <>
                  <span className="font-semibold">Not ready to finalize or publish: </span>
                  {readiness}
                </>
              ) : (
                <span className="flex items-center gap-2 font-semibold">
                  <CheckCircle2 className="size-4" /> Ready to finalize a response.
                </span>
              )}
            </div>
          )}

          {!completed && (
            <form onSubmit={finalize} className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label htmlFor="response-text" className="text-sm font-semibold">
                  Response text
                </label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void loadDraft()}
                  disabled={busy !== null}
                >
                  {busy === "draft" ? "Building…" : "Start from generated draft"}
                </Button>
              </div>
              <Textarea
                id="response-text"
                value={responseText}
                onChange={(event) => setResponseText(event.target.value)}
                placeholder="Write the response the patient will read."
                className="min-h-48 font-mono text-sm"
                maxLength={20000}
              />
              {draftNotice && <p className="text-xs text-muted-foreground">{draftNotice}</p>}
              <Button
                type="submit"
                disabled={busy !== null || !responseText.trim() || readiness !== null}
                className="gap-2"
              >
                {busy === "finalize" && <LoaderCircle className="size-4 animate-spin" />}
                Finalize as version {(latestVersion?.version ?? 0) + 1}
              </Button>
            </form>
          )}

          <div className="space-y-3 border-t pt-5">
            <h3 className="text-sm font-semibold">Finalized versions</h3>
            {versions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No version has been finalized yet.</p>
            ) : (
              <ol className="space-y-3">
                {versions.map((version) => {
                  const isLatest = version.id === latestVersion?.id;
                  return (
                    <li key={version.id} className="rounded-lg border p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-medium">
                          Version {version.version}
                          {isLatest && <span className="text-muted-foreground"> · latest</span>}
                        </p>
                        {version.publication ? (
                          <Badge variant="secondary">
                            Published by {version.publication.published_by} ·{" "}
                            {formatDate(version.publication.published_at)}
                          </Badge>
                        ) : (
                          <Badge variant="outline">Not visible to the patient</Badge>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Finalized by {version.finalized_by} · {formatDate(version.finalized_at)}
                      </p>
                      <details className="mt-2">
                        <summary className="cursor-pointer text-sm font-medium">Show text</summary>
                        <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-muted p-3 text-sm leading-6">
                          {version.content}
                        </pre>
                      </details>
                      {isLatest && !version.publication && !completed && (
                        <Button
                          className="mt-3 gap-2"
                          onClick={() => publish(version.id, version.version)}
                          disabled={busy !== null || readiness !== null}
                        >
                          {busy === `publish-${version.id}` ? (
                            <LoaderCircle className="size-4 animate-spin" />
                          ) : (
                            <Send className="size-4" />
                          )}
                          Publish to patient portal
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
            {latestVersion && !latestVersion.publication && !completed && (
                <p className="text-xs text-muted-foreground">
                  Outcomes of Partially fulfilled, Rejected, or Retained, and cases flagged for
                  review, need an approved independent review of the latest version before
                  publishing. Request it with the workflow action “Request independent review”.
                </p>
              )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
