import { ZepClient } from "@getzep/zep-cloud";
import { history } from "./chat_shoe_store_history";

async function waitForTask(client: ZepClient, taskUuid: string) {
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline) {
        const task = await client.task.get(taskUuid);
        if (task.status === "succeeded") return;
        if (task.status === "failed" || task.status === "partial") {
            throw new Error(`Message ingestion task ${task.status}`);
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    throw new Error("Timed out waiting for messages to process.");
}

async function main() {
    const projectApiKey = process.env.ZEP_API_KEY;
    if (!projectApiKey) throw new Error("ZEP_API_KEY is required");

    const client = new ZepClient({
        apiKey: projectApiKey,
    });

    const runId = Date.now();
    const user = await client.user.create({
        userId: `typescript-memory-${runId}`,
        metadata: { role: "admin" },
        email: `amy+${runId}@acme.com`,
        firstName: "Amy",
        lastName: "Wu",
    });
    const userUuid = user.uuid;
    if (!userUuid) throw new Error("Zep did not return a user UUID");
    console.log(`Created user ${userUuid}`);

    const thread = await client.thread.create({ userUuid });
    const threadUuid = thread.uuid;
    if (!threadUuid) throw new Error("Zep did not return a thread UUID");
    console.log(`Created thread ${threadUuid}`);

    const added = await client.thread.addMessages(threadUuid, {
        messages: history,
    });
    if (added.task?.uuid) await waitForTask(client, added.task.uuid);

    const memory = await client.thread.getContext(threadUuid);
    console.log("Context: ", memory.context);

    const threadMessages = await client.thread.listMessages(threadUuid, {
        pageSize: 10,
    });
    console.log("Thread messages: ", JSON.stringify(threadMessages.data));
}

main();
