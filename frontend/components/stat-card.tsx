import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";

interface StatCardProps {
  label: string;
  value: string | number;
  note: string;
  icon: LucideIcon;
  accent?: "teal" | "amber" | "red" | "blue";
}

const accentStyles = {
  teal: "bg-emerald-50 text-emerald-800",
  amber: "bg-amber-50 text-amber-800",
  red: "bg-rose-50 text-rose-800",
  blue: "bg-sky-50 text-sky-800",
};

export function StatCard({
  label,
  value,
  note,
  icon: Icon,
  accent = "teal",
}: StatCardProps) {
  return (
    <Card className="border-0 shadow-sm ring-1 ring-border/80">
      <CardContent className="flex items-start justify-between gap-3 p-4 md:p-5">
        <div>
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-2xl font-semibold tracking-tight">{value}</p>
          <p className="mt-1 text-xs text-muted-foreground">{note}</p>
        </div>
        <div className={`flex size-10 shrink-0 items-center justify-center rounded-xl ${accentStyles[accent]}`}>
          <Icon className="size-[18px]" />
        </div>
      </CardContent>
    </Card>
  );
}
