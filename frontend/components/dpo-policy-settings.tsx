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
    requestType: string,
    field: "internal_target_days" | "legal_deadline_input" | "reason",
    value: number | string,
  ) {
    setPolicies((current) =>
      current.map((policy) =>
        policy.request_type === requestType
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

    setSavingType(policy.request_type);
    try {
      const response = await fetch("/api/dpo/policies", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_type: policy.request_type,
          internal_target_days: policy.internal_target_days,
          legal_deadline_days: legalDeadlineDays,
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
      setPolicies((current) =>
        current.map((item) =>
          item.request_type === body.request_type
            ? {
                ...body,
                legal_deadline_input:
                  body.legal_deadline_days === null
                    ? ""
                    : String(body.legal_deadline_days),
                reason: "",
              }
            : item,
        ),
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
            Configure internal targets and approved legal deadlines by request
            type. A blank legal deadline means no legal period has been
            configured; it is not inferred from the internal target.
          </p>
        </section>

        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
          Internal targets use calendar days and apply only to requests
          submitted after a policy change. Existing request dates are preserved.
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
                  key={policy.request_type}
                  className="border-0 shadow-sm ring-1 ring-border/80"
                >
                  <CardHeader>
                    <CardTitle>{policy.request_type_label}</CardTitle>
                    <CardDescription>
                      Changes require a reason and are recorded in the audit
                      history.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <form
                      onSubmit={(event) => void savePolicy(event, policy)}
                      className="space-y-4"
                    >
                      <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                          <Label htmlFor={`${policy.request_type}-internal`}>
                            Internal target (calendar days)
                          </Label>
                          <Input
                            id={`${policy.request_type}-internal`}
                            type="number"
                            min={1}
                            max={3650}
                            required
                            value={policy.internal_target_days}
                            onChange={(event) =>
                              updatePolicy(
                                policy.request_type,
                                "internal_target_days",
                                Number(event.target.value),
                              )
                            }
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor={`${policy.request_type}-legal`}>
                            Legal deadline (calendar days)
                          </Label>
                          <Input
                            id={`${policy.request_type}-legal`}
                            type="number"
                            min={1}
                            max={3650}
                            value={policy.legal_deadline_input}
                            onChange={(event) =>
                              updatePolicy(
                                policy.request_type,
                                "legal_deadline_input",
                                event.target.value,
                              )
                            }
                            placeholder="Not configured"
                          />
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`${policy.request_type}-reason`}>
                          Reason for change
                        </Label>
                        <Textarea
                          id={`${policy.request_type}-reason`}
                          maxLength={2000}
                          required
                          value={policy.reason}
                          onChange={(event) =>
                            updatePolicy(
                              policy.request_type,
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
                        {savingType === policy.request_type ? (
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
