import { redirect } from "next/navigation";

import { DpoOverview } from "@/components/dpo-overview";
import { getDemoSession } from "@/lib/session";

export default async function DpoPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "dpo") redirect("/login");
  return <DpoOverview principalId={session.principalId} />;
}
