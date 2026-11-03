// A sum initialized to zero does not mean usage was reported.
export function tokenUsageText(total: number | null | undefined, calls: number, missing: number): string {
  if (total == null || !Number.isFinite(total) || (calls > 0 && missing >= calls)) return "미계측";
  return total.toLocaleString("ko-KR");
}
