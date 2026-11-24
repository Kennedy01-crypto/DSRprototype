"use client";

import { useEffect, useState, type FormEvent } from "react";
import { LoaderCircle, Save } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { DSRDeadlinePolicy } from "@/lib/types";

type EditablePolicy = DSRDeadlinePolicy & {
  legal_deadline_input: string;
  pause_reasons_input: string;
  reason: string;
};

export function DpoPolicySettings({ principalId }: { principalId: string }) {
  const [policies, setPolicies] = useState<EditablePolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingType, setSavingType] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function loadPolicies() {
      try {
        const response = await fetch("/api/dpo/policies", { cache: "no-store" });
        const body = (await response.json()) as
          | DSRDeadlinePolicy[]
          | { detail?: string };
        if (!response.ok || !Array.isArray(body)) {
          throw new Error(
            !Array.isArray(body) && body.detail
              ? body.detail
              : "Could not load deadline policies.",
          );
        }
        if (!cancelled) {
          setPolicies(
            body.map((policy) => ({
              ...policy,
              legal_deadline_input:
                policy.legal_deadline_days === null
                  ? ""
                  : String(policy.legal_deadline_days),
              pause_reasons_input: policy.pause_reasons.join(", "),
              reason: "",
            })),
          );
        }
      } catch (error) {
        if (!cancelled) {
          toast.error(
            error instanceof Error
              ? error.message
              : "Could not load deadline policies.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadPolicies();
    return () => {
      cancelled = true;
    };
  }, []);

  function updatePolicy(
    policyId: number,
    field:
      | "jurisdiction_code"
      | "internal_target_days"
      | "legal_deadline_input"
      | "policy_source"
      | "calendar_days"
      | "extension_allowed"
      | "maximum_extension_days"
      | "pause_allowed"
      | "pause_reasons_input"
      | "notification_required"
      | "reason",
    value: number | string | boolean,
  ) {
    setPolicies((current) =>
      current.map((policy) =>
        policy.id === policyId
          ? { ...policy, [field]: value }
          : policy,
      ),
    );
  }

  async function savePolicy(
    event: FormEvent<HTMLFormElement>,
    policy: EditablePolicy,
  ) {
    event.preventDefault();
    if (!policy.reason.trim()) {
      toast.error("Record why this deadline policy is changing.");
      return;
    }
    const legalDeadlineDays = policy.legal_deadline_input.trim()
      ? Number(policy.legal_deadline_input)
      : null;
    if (
      legalDeadlineDays !== null &&
      (!Number.isInteger(legalDeadlineDays) ||
        legalDeadlineDays < 1 ||
        legalDeadlineDays > 3650)
    ) {
      toast.error("Enter a legal deadline between 1 and 3650 days, or leave it blank.");
      return;
    }

    const policyKey = `${policy.jurisdiction_code}:${policy.request_type}`;
    setSavingType(policyKey);
    try {
      const response = await fetch("/api/dpo/policies", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_type: policy.request_type,
          jurisdiction_code: policy.jurisdiction_code,
          internal_target_days: policy.internal_target_days,
          legal_deadline_days: legalDeadlineDays,
          policy_source: policy.policy_source,
          clock_start_event: "SUBMITTED",
          calendar_days: policy.calendar_days,
          extension_allowed: policy.extension_allowed,
          maximum_extension_days: policy.maximum_extension_days,
          pause_allowed: policy.pause_allowed,
          pause_reasons: policy.pause_reasons_input
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          notification_required: policy.notification_required,
          reason: policy.reason.trim(),
        }),
      });
      const body = (await response.json()) as
        | DSRDeadlinePolicy
        | { detail?: string };
      if (!response.ok || !("request_type" in body)) {
        throw new Error(
          "detail" in body ? body.detail : "Could not save this deadline policy.",
        );
      }
      const refreshedPolicies = await fetch("/api/dpo/policies", {
        cache: "no-store",
      });
      const refreshedBody = (await refreshedPolicies.json()) as DSRDeadlinePolicy[];
      if (!refreshedPolicies.ok) {
        throw new Error("Policy was saved but the updated policy list could not be refreshed.");
      }
      setPolicies(
        refreshedBody.map((item) => ({
          ...item,
          legal_deadline_input:
            item.legal_deadline_days === null
              ? ""
              : String(item.legal_deadline_days),
          pause_reasons_input: item.pause_reasons.join(", "),
          reason: "",
        })),
      );
      toast.success(`${body.request_type_label} deadline policy saved.`);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not save this deadline policy.",
      );
    } finally {
      setSavingType("");
    }
  }

  return (
    <AppShell role="dpo" principalId={principalId}>
      <div className="space-y-6">
        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            PRIVACY OPERATIONS
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">
            Deadline policy
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            Configure immutable, versioned deadline profiles by jurisdiction
            and request type. A blank legal deadline means no legal period has
            been configured; it is not inferred from the internal target.
          </p>
        </section>

        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
          Deadline policy versions are snapshotted onto cases when submitted.
          Existing cases retain their selected policy version and dates.
          Legal deadline values should only be entered after approval by the
          organization&apos;s privacy/legal owner. Waiting for internal teams or
          subject information does not pause either clock.
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          {loading
            ? Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-72 w-full" />
              ))
            : policies.map((policy) => (
                <Card
                  key={policy.id}
                  className="border-0 shadow-sm ring-1 ring-border/80"
                >
                  <CardHeader>
                    <CardTitle>{policy.request_type_label}</CardTitle>
                    <CardDescription>
                      {policy.jurisdiction_code} · version {policy.version} ·
                      effective {new Date(policy.effective_at).toLocaleDateString()}.
                      Changes create a new audited version.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <form
                      onSubmit={(event) => void savePolicy(event, policy)}
                      className="space-y-4"
                    >
                      <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                          <Label htmlFor={`${policy.id}-jurisdiction`}>
                            Jurisdiction code
                          </Label>
                          <Input
                            id={`${policy.id}-jurisdiction`}
                            value={policy.jurisdiction_code}
                            maxLength={8}
                            pattern="[A-Z]{2,8}"
                            required
                            onChange={(event) =>
                              updatePolicy(
                                policy.id,
                                "jurisdiction_code",
                                event.target.value.toUpperCase(),
                              )
                            }
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor={`${policy.id}-internal`}>
                            Internal target ({policy.calendar_days ? "calendar" : "business"} days)
                          </Label>
                          <Input
                            id={`${policy.id}-internal`}
                            type="number"
                            min={1}
                            max={3650}
                            required
                            value={policy.internal_target_days}
                            onChange={(event) =>
                              updatePolicy(
                                policy.id,
                                "internal_target_days",
                                Number(event.target.value),
                              )
                            }
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor={`${policy.id}-legal`}>
                            Legal deadline ({policy.calendar_days ? "calendar" : "business"} days)
                          </Label>
                          <Input
                            id={`${policy.id}-legal`}
                            type="number"
                            min={1}
                            max={3650}
                            value={policy.legal_deadline_input}
                            onChange={(event) =>
                              updatePolicy(
                                policy.id,
                                "legal_deadline_input",
                                event.target.value,
                              )
                            }
                            placeholder="Not configured"
                          />
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`${policy.id}-source`}>
                          Policy source / authority reference
                        </Label>
                        <Input
                          id={`${policy.id}-source`}
                          value={policy.policy_source}
                          maxLength={255}
                          onChange={(event) =>
                            updatePolicy(policy.id, "policy_source", event.target.value)
                          }
                        />
                      </div>
                      <label className="flex items-start gap-2 text-sm">
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={!policy.calendar_days}
                          onChange={(event) =>
                            updatePolicy(policy.id, "calendar_days", !event.target.checked)
                          }
                        />
                        <span>
                          Count business days
                          <span className="block text-xs text-muted-foreground">
                            Skip weekends and the public holidays configured on the server. Applies
                            to new requests and to extensions.
                          </span>
                        </span>
                      </label>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={policy.extension_allowed}
                            onChange={(event) =>
                              updatePolicy(policy.id, "extension_allowed", event.target.checked)
                            }
                          />
                          Allow authorized extensions
                        </label>
                        <div className="space-y-2">
                          <Label htmlFor={`${policy.id}-max-extension`}>
                            Maximum extension (days)
                          </Label>
                          <Input
                            id={`${policy.id}-max-extension`}
                            type="number"
                            min={policy.extension_allowed ? 1 : 0}
                            max={3650}
                            value={policy.maximum_extension_days}
                            onChange={(event) =>
                              updatePolicy(
                                policy.id,
                                "maximum_extension_days",
                                Number(event.target.value),
                              )
                            }
                          />
                        </div>
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={policy.pause_allowed}
                            onChange={(event) =>
                              updatePolicy(policy.id, "pause_allowed", event.target.checked)
                            }
                          />
                          Allow authorized clock pauses (stop and restart)
                        </label>
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={policy.notification_required}
                            onChange={(event) =>
                              updatePolicy(
                                policy.id,
                                "notification_required",
                                event.target.checked,
                              )
                            }
                          />
                          Require patient notification
                        </label>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`${policy.id}-pause-reasons`}>
                          Approved pause reasons (comma-separated)
                        </Label>
                        <Input
                          id={`${policy.id}-pause-reasons`}
                          value={policy.pause_reasons_input}
                          onChange={(event) =>
                            updatePolicy(
                              policy.id,
                              "pause_reasons_input",
                              event.target.value,
                            )
                          }
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`${policy.id}-reason`}>
                          Reason for change
                        </Label>
                        <Textarea
                          id={`${policy.id}-reason`}
                          maxLength={2000}
                          required
                          value={policy.reason}
                          onChange={(event) =>
                            updatePolicy(
                              policy.id,
                              "reason",
                              event.target.value,
                            )
                          }
                          placeholder="Record the approval or operational reason."
                          className="min-h-20"
                        />
                      </div>
                      <Button
                        type="submit"
                        disabled={savingType !== ""}
                        className="gap-2"
                      >
                        {savingType === `${policy.jurisdiction_code}:${policy.request_type}` ? (
                          <LoaderCircle className="size-4 animate-spin" />
                        ) : (
                          <Save className="size-4" />
                        )}
                        Save policy
                      </Button>
                    </form>
                  </CardContent>
                </Card>
              ))}
        </div>
      </div>
    </AppShell>
  );
}
