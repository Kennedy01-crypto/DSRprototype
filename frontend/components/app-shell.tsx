"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Bell,
  ClipboardList,
  FileText,
  HeartPulse,
  LogOut,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { DemoRole } from "@/lib/types";

interface AppShellProps {
  role: DemoRole;
  principalId: string;
  children: React.ReactNode;
}

const navItems = {
  dpo: [
    { label: "Request queue", href: "/dpo", icon: ClipboardList },
  ],
  patient: [
    { label: "My requests", href: "/patient", icon: FileText },
  ],
};

export function AppShell({ role, principalId, children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const dpo = role === "dpo";

  async function signOut() {
    const response = await fetch("/api/session", { method: "DELETE" });
    if (!response.ok) {
      toast.error("Could not end the demo session.");
      return;
    }
    toast.success("Signed out of the mock workspace.");
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-[258px] flex-col bg-sidebar text-sidebar-foreground lg:flex">
        <div className="flex h-[76px] items-center gap-3 px-6">
          <div className="flex size-10 items-center justify-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
            <HeartPulse className="size-5" />
          </div>
          <div>
            <p className="text-sm font-semibold tracking-wide">AAR HEALTHCARE</p>
            <p className="text-xs text-sidebar-foreground/60">Privacy operations</p>
          </div>
        </div>
        <Separator className="bg-sidebar-border" />
        <div className="px-4 pt-6">
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-sidebar-foreground/50">
            Workspace
          </p>
          <nav aria-label="Main navigation" className="space-y-1">
            {navItems[role].map(({ label, href, icon: Icon }) => {
              const active = pathname === href;
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-sidebar-foreground/75 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground"
                  }`}
                >
                  <Icon className="size-[17px]" />
                  {label}
                </Link>
              );
            })}
          </nav>
        </div>
        {dpo && (
          <div className="mt-auto px-4 pb-5">
            <div className="rounded-xl border border-sidebar-border bg-white/5 p-4">
              <div className="mb-2 flex items-center gap-2 text-sidebar-primary">
                <ShieldCheck className="size-4" />
                <span className="text-xs font-semibold">Prototype environment</span>
              </div>
              <p className="text-xs leading-5 text-sidebar-foreground/65">
                Mock workflow only. No source systems or real patient records.
              </p>
            </div>
          </div>
        )}
      </aside>

      <div className="lg:pl-[258px]">
        <header className="sticky top-0 z-10 flex h-[68px] items-center justify-between border-b bg-white/90 px-4 backdrop-blur md:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary lg:hidden">
              <HeartPulse className="size-5" />
            </div>
            <div>
              <p className="text-sm font-semibold">{dpo ? "DPO workspace" : "Patient portal"}</p>
              <p className="hidden text-xs text-muted-foreground sm:block">
                Mock identity: {principalId}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 sm:flex">
              <Activity className="size-3.5" />
              Demo mode
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Notifications are not enabled in this prototype"
              title="Notifications are not enabled"
              disabled
            >
              <Bell />
            </Button>
            <Button variant="outline" size="sm" onClick={signOut} className="gap-2">
              <LogOut className="size-4" />
              <span className="hidden sm:inline">Sign out</span>
            </Button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1440px] px-4 py-7 md:px-8 md:py-9">
          <div className="mb-6 flex items-start justify-between gap-4 lg:hidden">
            <nav className="flex gap-2" aria-label="Mobile navigation">
              {navItems[role].map(({ label, href, icon: Icon }) => (
                <Link
                  key={href}
                  href={href}
                  className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
                    pathname === href ? "bg-primary text-primary-foreground" : "bg-white"
                  }`}
                >
                  <Icon className="size-4" />
                  {label}
                </Link>
              ))}
              {!dpo && (
                <Link
                  href="/patient#new-request"
                  className="inline-flex items-center gap-2 rounded-lg border bg-white px-3 py-2 text-sm"
                >
                  <Plus className="size-4" />
                  New request
                </Link>
              )}
            </nav>
          </div>
          {children}
        </main>
      </div>
    </div>
  );
}
