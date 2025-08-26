import dayjs from "dayjs";

/**
 * Format a date string into a readable format
 */
export function formatDate(
  dateString?: string | null,
  format: string = "MMM D, YYYY"
): string {
  if (!dateString) return "Unknown";

  try {
    return dayjs(dateString).format(format);
  } catch (error) {
    console.error("Error formatting date:", error);
    return "Invalid date";
  }
}
