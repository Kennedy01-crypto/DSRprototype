import { redirect } from "next/navigation";

import { getDemoSession } from "@/lib/session";

export default async function HomePage() {
  const session = await getDemoSession();
  if (!session) redirect("/login");
  const destination = {
    patient: "/patient",
    dpo: "/dpo",
    admin: "/admin",
    department_contributor: "/department",
    independent_reviewer: "/reviewer",
  } as const;
  redirect(destination[session.role]);
}
