import { redirect } from "next/navigation";

import { IntegrationQueue } from "@/components/integration-queue";
import { getDemoSession } from "@/lib/session";

export default async function DpoIntegrationsPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "dpo") redirect("/login");
  return <IntegrationQueue principalId={session.principalId} />;
}
