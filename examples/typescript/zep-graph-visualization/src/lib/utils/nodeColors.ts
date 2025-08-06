import colors from "tailwindcss/colors";

// Define a color palette for node coloring
export const nodeColorPalette = {
  light: [
    colors.pink[500], // Entity (default)
    colors.blue[500],
    colors.emerald[500],
    colors.amber[500],
    colors.indigo[500],
    colors.orange[500],
    colors.teal[500],
    colors.purple[500],
    colors.cyan[500],
    colors.lime[500],
    colors.rose[500],
    colors.violet[500],
    colors.green[500],
    colors.red[500],
  ],
  dark: [
    colors.pink[400], // Entity (default)
    colors.blue[400],
    colors.emerald[400],
    colors.amber[400],
    colors.indigo[400],
    colors.orange[400],
    colors.teal[400],
    colors.purple[400],
    colors.cyan[400],
    colors.lime[400],
    colors.rose[400],
    colors.violet[400],
    colors.green[400],
    colors.red[400],
  ],
};

// Function to create a map of label to color index
export function createLabelColorMap(labels: string[]) {
  // Start with Entity mapped to first color
  const result = new Map<string, number>();
  result.set("Entity", 0);

  // Sort all non-Entity labels alphabetically for consistent color assignment
  const sortedLabels = labels
    .filter((label) => label !== "Entity")
    .sort((a, b) => a.localeCompare(b));

  // Map each unique label to a color index
  let nextIndex = 1;
  sortedLabels.forEach((label) => {
    if (!result.has(label)) {
      result.set(label, nextIndex % nodeColorPalette.light.length);
      nextIndex++;
    }
  });

  return result;
}

// Get color for a label directly
export function getNodeColor(
  label: string | null | undefined,
  isDarkMode: boolean,
  labelColorMap: Map<string, number>
): string {
  if (!label) {
    return isDarkMode ? nodeColorPalette.dark[0] : nodeColorPalette.light[0];
  }

  // If label is "Entity" or not found in the map, return default color
  if (label === "Entity" || !labelColorMap.has(label)) {
    return isDarkMode ? nodeColorPalette.dark[0] : nodeColorPalette.light[0];
  }

  // Get the color index for this label
  const colorIndex = labelColorMap.get(label) || 0;

  // Return the color from the appropriate theme palette
  return isDarkMode
    ? nodeColorPalette.dark[colorIndex]
    : nodeColorPalette.light[colorIndex];
}
