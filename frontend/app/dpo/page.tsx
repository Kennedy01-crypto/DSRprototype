import { redirect } from "next/navigation";

import { DpoDashboard } from "@/components/dpo-dashboard";
import { getDemoSession } from "@/lib/session";

export default async function DpoPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "dpo") redirect("/login");
  return <DpoDashboard principalId={session.principalId} />;
}
