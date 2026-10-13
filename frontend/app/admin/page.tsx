import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getDemoSession } from "@/lib/session";

export default async function AdminPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "admin") redirect("/login");

  return (
    <AppShell role="admin" principalId={session.principalId}>
      <Card className="max-w-2xl border-0 shadow-sm ring-1 ring-border/80">
        <CardHeader>
          <CardTitle>Administration</CardTitle>
          <CardDescription>
            Mock administrator workspace
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
          <p>
            This role is separate from DPO casework and cannot access request
            records or deadline policy editing.
          </p>
          <p>
            User administration is not available in this prototype. It will be
            connected to the organization’s identity system in a later phase.
          </p>
        </CardContent>
      </Card>
    </AppShell>
  );
}
