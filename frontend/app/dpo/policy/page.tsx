import { redirect } from "next/navigation";

import { DpoPolicySettings } from "@/components/dpo-policy-settings";
import { getDemoSession } from "@/lib/session";

export default async function DpoPolicyPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "dpo") redirect("/login");
  return <DpoPolicySettings principalId={session.principalId} />;
}
