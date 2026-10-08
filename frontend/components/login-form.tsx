"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, HeartPulse, LockKeyhole, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { DemoRole } from "@/lib/types";

export function LoginForm() {
  const router = useRouter();
  const [role, setRole] = useState<DemoRole>("patient");
  const [principalId, setPrincipalId] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, principalId: principalId.trim() }),
      });
      const body = (await response.json()) as { detail?: string };
      if (!response.ok) {
        toast.error(body.detail ?? "Could not start the demo session.");
        return;
      }
      toast.success(`Opened the mock ${role} workspace.`);
      router.push(role === "dpo" ? "/dpo" : "/patient");
      router.refresh();
    } catch {
      toast.error("The UI service could not complete sign-in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_.95fr]">
      <section className="relative hidden overflow-hidden bg-[#173e4d] px-12 py-14 text-white lg:flex lg:flex-col lg:justify-between xl:px-20">
        <div className="absolute -right-32 -top-32 size-[520px] rounded-full border border-white/10" />
        <div className="absolute -right-12 -top-12 size-[280px] rounded-full border border-white/10" />
        <div className="relative flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl bg-[#b9e1d0] text-[#173e4d]">
            <HeartPulse className="size-6" />
          </div>
          <div>
            <p className="text-sm font-semibold tracking-[0.12em]">AAR HEALTHCARE</p>
            <p className="text-xs text-white/60">Privacy operations</p>
          </div>
        </div>
        <div className="relative max-w-xl pb-10">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white/75">
            <ShieldCheck className="size-4 text-[#b9e1d0]" />
            Data subject request workspace
          </div>
          <h1 className="text-4xl font-semibold leading-tight tracking-tight xl:text-5xl">
            Privacy requests,
            <br />
            handled with care.
          </h1>
          <p className="mt-5 max-w-md text-base leading-7 text-white/70">
            A prototype workspace for tracking data requests through review,
            decisions, and patient updates.
          </p>
          <div className="mt-10 flex items-center gap-5 text-xs text-white/55">
            <span className="flex items-center gap-2"><span className="size-2 rounded-full bg-[#8ed0ad]" />Workflow visibility</span>
            <span className="flex items-center gap-2"><span className="size-2 rounded-full bg-[#8ed0ad]" />Auditable actions</span>
          </div>
        </div>
        <p className="relative text-xs text-white/45">Synthetic demo only · Not connected to clinical systems</p>
      </section>

      <section className="flex items-center justify-center px-5 py-12 sm:px-8">
        <div className="w-full max-w-[440px]">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <HeartPulse className="size-5" />
            </div>
            <div>
              <p className="text-sm font-semibold">AAR Healthcare</p>
              <p className="text-xs text-muted-foreground">Privacy operations</p>
            </div>
          </div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-primary">Welcome</p>
          <h2 className="text-3xl font-semibold tracking-tight">Sign in to the demo</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Choose a workspace and enter a fictional identifier to continue.
          </p>

          <Card className="mt-7 border-0 shadow-[0_16px_50px_-24px_rgba(22,55,69,.25)] ring-1 ring-border">
            <CardHeader>
              <CardTitle>Demo identity</CardTitle>
              <CardDescription>This does not verify your real identity.</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit} className="space-y-5">
                <fieldset>
                  <legend className="mb-2 text-sm font-medium">Workspace</legend>
                  <div className="grid grid-cols-2 gap-2 rounded-xl bg-muted p-1">
                    {(["patient", "dpo"] as const).map((item) => (
                      <button
                        key={item}
                        type="button"
                        aria-pressed={role === item}
                        onClick={() => setRole(item)}
                        className={`rounded-lg px-3 py-2.5 text-sm font-medium capitalize transition ${
                          role === item
                            ? "bg-white text-foreground shadow-sm"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {item === "dpo" ? "DPO workspace" : "Patient portal"}
                      </button>
                    ))}
                  </div>
                </fieldset>
                <div className="space-y-2">
                  <Label htmlFor="principal-id">Mock patient or staff ID</Label>
                  <Input
                    id="principal-id"
                    autoComplete="off"
                    value={principalId}
                    onChange={(event) => setPrincipalId(event.target.value)}
                    placeholder={role === "patient" ? "e.g. mock-patient-access" : "e.g. mock-reviewer"}
                    minLength={1}
                    maxLength={128}
                    pattern="[A-Za-z0-9_.:\x2d]+"
                    required
                  />
                  <p className="text-xs leading-5 text-muted-foreground">
                    Use only a seeded mock ID or another fictional identifier.
                  </p>
                </div>
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-5 text-amber-900">
                  Never enter real patient IDs, clinical details, or credentials.
                </div>
                <Button type="submit" className="h-10 w-full gap-2" disabled={busy}>
                  <LockKeyhole className="size-4" />
                  {busy ? "Opening workspace…" : "Continue to demo"}
                  {!busy && <ArrowRight className="ml-auto size-4" />}
                </Button>
              </form>
            </CardContent>
          </Card>
          <p className="mt-5 text-center text-xs text-muted-foreground">
            Mock authentication only · No PARAS, CIMS, or ERPS connection
          </p>
        </div>
      </section>
    </div>
  );
}
