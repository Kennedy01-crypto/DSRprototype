"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Bell,
  ClipboardList,
  ChevronLeft,
  ChevronRight,
  LayoutDashboard,
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
    { label: "Overview", href: "/dpo", icon: LayoutDashboard },
    { label: "Request queue", href: "/dpo/requests", icon: ClipboardList },
  ],
  patient: [
    { label: "My requests", href: "/patient", icon: FileText },
  ],
};

const SIDEBAR_STORAGE_KEY = "dsr-sidebar-expanded";
const SIDEBAR_CHANGE_EVENT = "dsr-sidebar-preference-change";

function subscribeSidebar(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(SIDEBAR_CHANGE_EVENT, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(SIDEBAR_CHANGE_EVENT, listener);
  };
}

function getSidebarExpanded(): boolean {
  return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) !== "false";
}

function getServerSidebarExpanded(): boolean {
  return true;
}

function updateSidebarExpanded(expanded: boolean) {
  window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(expanded));
  window.dispatchEvent(new Event(SIDEBAR_CHANGE_EVENT));
}

export function AppShell({ role, principalId, children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const dpo = role === "dpo";
  const sidebarExpanded = useSyncExternalStore(
    subscribeSidebar,
    getSidebarExpanded,
    getServerSidebarExpanded,
  );

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
      <aside
        className={`fixed inset-y-0 left-0 z-20 hidden flex-col bg-sidebar text-sidebar-foreground transition-[width] duration-200 lg:flex ${
          sidebarExpanded ? "w-[258px]" : "w-[76px]"
        }`}
      >
        <div className={`flex h-[76px] items-center gap-3 ${sidebarExpanded ? "px-6" : "justify-center px-2"}`}>
          <div className="flex size-10 items-center justify-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
            <HeartPulse className="size-5" />
          </div>
          {sidebarExpanded && <div>
            <p className="text-sm font-semibold tracking-wide">AAR HEALTHCARE</p>
            <p className="text-xs text-sidebar-foreground/60">Privacy operations</p>
          </div>}
        </div>
        <Separator className="bg-sidebar-border" />
        <div className={`pt-6 ${sidebarExpanded ? "px-4" : "px-2"}`}>
          {sidebarExpanded && (
            <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-sidebar-foreground/50">
              Workspace
            </p>
          )}
          <nav aria-label="Main navigation" className="space-y-1">
            {navItems[role].map(({ label, href, icon: Icon }) => {
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  aria-label={sidebarExpanded ? undefined : label}
                  title={sidebarExpanded ? undefined : label}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-sidebar-foreground/75 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground"
                  } ${sidebarExpanded ? "" : "justify-center px-0"}`}
                >
                  <Icon className="size-[17px]" />
                  {sidebarExpanded && label}
                </Link>
              );
            })}
          </nav>
        </div>
        {dpo && (
          <div className={`mt-auto pb-5 ${sidebarExpanded ? "px-4" : "px-2"}`}>
            <div className="rounded-xl border border-sidebar-border bg-white/5 p-4">
              <div className={`mb-2 flex items-center gap-2 text-sidebar-primary ${sidebarExpanded ? "" : "justify-center"}`}>
                <ShieldCheck className="size-4" />
                {sidebarExpanded && <span className="text-xs font-semibold">Prototype environment</span>}
              </div>
              {sidebarExpanded && <p className="text-xs leading-5 text-sidebar-foreground/65">
                Mock workflow only. No source systems or real patient records.
              </p>}
            </div>
          </div>
        )}
        <Button
          variant="ghost"
          size="icon"
          aria-label={sidebarExpanded ? "Collapse sidebar" : "Expand sidebar"}
          title={sidebarExpanded ? "Collapse sidebar" : "Expand sidebar"}
          onClick={() => updateSidebarExpanded(!sidebarExpanded)}
          className="absolute right-[-14px] top-[88px] z-30 size-7 rounded-full border border-sidebar-border bg-sidebar text-sidebar-foreground shadow-md hover:bg-sidebar-accent"
        >
          {sidebarExpanded ? <ChevronLeft className="size-4" /> : <ChevronRight className="size-4" />}
        </Button>
      </aside>

      <div className={`transition-[padding] duration-200 ${sidebarExpanded ? "lg:pl-[258px]" : "lg:pl-[76px]"}`}>
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
                    pathname === href || pathname.startsWith(`${href}/`) ? "bg-primary text-primary-foreground" : "bg-white"
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
