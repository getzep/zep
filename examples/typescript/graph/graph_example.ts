import { ZepClient } from '@getzep/zep-cloud';

const API_KEY = process.env.ZEP_API_KEY;

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    console.log("Creating graph...");
    const graph = await client.graph.create({
        name: "My graph",
        description: "This is my graph",
    });
    const graphUuid = graph.uuid;
    if (!graphUuid) throw new Error("Zep did not return a graph UUID");
    console.log(`Graph ${graphUuid} created`, graph);

    const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
    const episodes = await Promise.all([
        client.graph.episode.add(graphUuid, {
            type: "text",
            data: "This is a test episode",
        }),
        client.graph.episode.add(graphUuid, {
            type: "text",
            data: "Eric Clapton is a rock star",
        }),
        client.graph.episode.add(graphUuid, {
            type: "json",
            data: jsonString,
        }),
    ]);

    const taskUuid = episodes.at(-1)?.task?.uuid;
    if (taskUuid) {
        const deadline = Date.now() + 1_200_000;
        let nextStatusReport = Date.now() + 30_000;
        let completed = false;
        while (Date.now() < deadline) {
            const task = await client.task.get(taskUuid);
            if (task.status === "succeeded") {
                completed = true;
                break;
            }
            if (task.status === "partial") {
                console.warn(`Warning: task ${taskUuid} ended with partial status.`);
                completed = true;
                break;
            }
            if (task.status === "failed") {
                throw new Error(`Graph ingestion task failed: ${task.error?.message ?? ""}`);
            }
            if (Date.now() >= nextStatusReport) {
                console.log(`Task ${taskUuid} status=${task.status}; still waiting.`);
                nextStatusReport = Date.now() + 30_000;
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        if (!completed) throw new Error("Timed out waiting for graph ingestion.");
    }

    console.log(`Getting nodes from graph ${graphUuid}...`);
    const nodes = await client.graph.node.list(graphUuid, { pageSize: 10 });
    console.log(`Nodes from graph ${graphUuid}`, nodes.data);

    console.log(`Getting edges from graph ${graphUuid}...`);
    const edges = await client.graph.edge.list(graphUuid, { pageSize: 10 });
    console.log(`Edges from graph ${graphUuid}`, edges.data);

    console.log(`Searching graph ${graphUuid}...`);
    const searchResults = await client.graph.searchEdges(graphUuid, {
        pageSize: 10,
        query: "Eric Clapton",
    });
    console.log(`Search results from graph ${graphUuid}`, searchResults.data);
}

main().catch(console.error);