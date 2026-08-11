import { defineTool } from "eve/tools";
import { z } from "zod";

/** Stand-in product tool — preferences in Zep should steer the model here. */
export default defineTool({
  description: "Call Service A (billing / checkout workflow).",
  inputSchema: z.object({
    action: z.string().min(1).max(200).describe("What to do in Service A"),
  }),
  async execute({ action }) {
    return {
      service: "A",
      status: "ok",
      message: `Service A completed: ${action}`,
    };
  },
});
