import { redirect } from "next/navigation";

import { LoginForm } from "@/components/login-form";
import { getDemoSession } from "@/lib/session";

export default async function LoginPage() {
  const session = await getDemoSession();
  if (session) {
    const destination = {
      patient: "/patient",
      dpo: "/dpo",
      admin: "/admin",
      department_contributor: "/department",
      independent_reviewer: "/reviewer",
    } as const;
    redirect(destination[session.role]);
  }
  return <LoginForm />;
}
