import { redirect } from "next/navigation";

import { DepartmentWorkQueue } from "@/components/department-work-queue";
import { getDemoSession } from "@/lib/session";

export default async function DepartmentPage() {
  const session = await getDemoSession();
  if (!session || session.role !== "department_contributor") redirect("/login");
  return <DepartmentWorkQueue principalId={session.principalId} />;
}
