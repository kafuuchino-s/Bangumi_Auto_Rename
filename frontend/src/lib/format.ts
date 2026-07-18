import type { Locale } from "@/i18n/locale";

export function formatNumber(value: number | null | undefined, locale: Locale): string {
  return new Intl.NumberFormat(locale).format(value ?? 0);
}

export function formatPercent(value: number | null | undefined, locale: Locale): string {
  if (value == null) return "-";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value) + "%";
}

export function formatBytes(value: number | null | undefined, locale: Locale): string {
  if (value == null || value < 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let amount = value;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(amount)} ${units[index]}`;
}

export function formatRange(start: number, end: number, locale: Locale): string {
  const left = formatNumber(start, locale);
  return start === end ? left : `${left}–${formatNumber(end, locale)}`;
}
