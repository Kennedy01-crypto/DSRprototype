import { redirect } from "next/navigation";

import { DpoDashboard } from "@/components/dpo-dashboard";
import { getDemoSession } from "@/lib/session";

type DpoQueuePageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function DpoQueuePage({
  searchParams,
}: DpoQueuePageProps) {
  const session = await getDemoSession();
  if (!session || session.role !== "dpo") redirect("/login");

  const params = await searchParams;
  return (
    <DpoDashboard
      principalId={session.principalId}
      initialFilters={{
        state: typeof params.state === "string" ? params.state : "",
        queue: typeof params.queue === "string" ? params.queue : "",
        department: typeof params.department === "string" ? params.department : "",
      }}
    />
  );
}
