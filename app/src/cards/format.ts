export function fmtDate(v?: string | number | null): string {
  if (!v) return "";
  const d = typeof v === "number" ? new Date(v) : new Date(v);
  if (isNaN(d.getTime())) return String(v);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const yest = new Date(now.getTime() - 864e5).toDateString() === d.toDateString();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay) return time;
  if (yest) return `Yesterday ${time}`;
  if (now.getTime() - d.getTime() < 6 * 864e5) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

export function fmtEventTime(start: string, allDay: boolean, end?: string): string {
  if (allDay) return "All day";
  const s = new Date(start);
  const t = (d: Date) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return end ? `${t(s)} – ${t(new Date(end))}` : t(s);
}

export function dayLabel(start: string): string {
  const d = new Date(start.length === 10 ? start + "T00:00:00" : start);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return "Today";
  if (new Date(now.getTime() + 864e5).toDateString() === d.toDateString()) return "Tomorrow";
  return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

export function initials(name: string): string {
  const p = name.replace(/["<>]/g, "").trim().split(/\s+/).filter(Boolean);
  return ((p[0]?.[0] ?? "?") + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase();
}

export function acctTag(a?: string | null): string {
  return a === "work" ? "WORK" : a === "personal" ? "PERSONAL" : "";
}

export function bytes(n?: string | number): string {
  const v = Number(n || 0);
  if (!v) return "";
  const u = ["B", "KB", "MB", "GB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(v) / Math.log(1024)));
  return `${(v / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function mimeLabel(m: string): string {
  if (m.includes("document")) return "DOC";
  if (m.includes("spreadsheet")) return "SHEET";
  if (m.includes("presentation")) return "SLIDES";
  if (m.includes("folder")) return "FOLDER";
  if (m.includes("pdf")) return "PDF";
  if (m.startsWith("image/")) return "IMG";
  return (m.split("/").pop() || "FILE").slice(0, 6).toUpperCase();
}
