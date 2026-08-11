import { defineTool } from "eve/tools";
import { z } from "zod";

/** Stand-in product tool — preferences in Zep should steer the model here. */
export default defineTool({
  description: "Call Service B (legacy / alternate billing workflow).",
  inputSchema: z.object({
    action: z.string().min(1).max(200).describe("What to do in Service B"),
  }),
  async execute({ action }) {
    return {
      service: "B",
      status: "ok",
      message: `Service B completed: ${action}`,
    };
  },
});
