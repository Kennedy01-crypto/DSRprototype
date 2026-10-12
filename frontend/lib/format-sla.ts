export function formatSlaDue(
  value: string,
  currentTime: number | null,
  fallbackSeconds: number,
): string {
  const remaining =
    currentTime === null
      ? fallbackSeconds * 1000
      : new Date(value).getTime() - currentTime;
  const hours = Math.ceil(Math.abs(remaining) / (60 * 60 * 1000));
  if (remaining <= 0) {
    const duration =
      hours >= 48
        ? `${Math.floor(hours / 24)} days`
        : hours >= 1
          ? `${hours} ${hours === 1 ? "hour" : "hours"}`
          : "less than an hour";
    return `Overdue by ${duration}`;
  }
  if (hours < 24) return `Due in ${hours} ${hours === 1 ? "hour" : "hours"}`;
  const days = Math.ceil(hours / 24);
  return `Due in ${days} ${days === 1 ? "day" : "days"}`;
}
