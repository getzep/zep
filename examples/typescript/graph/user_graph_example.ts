import { ZepClient } from '@getzep/zep-cloud';
import { history } from './conversations';

const API_KEY = process.env.ZEP_API_KEY

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    const user = await client.user.create({ firstName: "Paul" });
    const userUuid = user.uuid;
    if (!userUuid) throw new Error("Zep did not return a user UUID");
    console.log(`User ${userUuid} created`);

    const thread = await client.thread.create({ userUuid });
    const threadUuid = thread.uuid;
    if (!threadUuid) throw new Error("Zep did not return a thread UUID");
    console.log(`Thread ${threadUuid} created`);

    const messageResult = await client.thread.addMessages(threadUuid, {
        messages: history[2],
    });
    if (messageResult.task?.uuid) {
        const deadline = Date.now() + 180_000;
        let completed = false;
        while (Date.now() < deadline) {
            const task = await client.task.get(messageResult.task.uuid);
            if (task.status === "succeeded") {
                completed = true;
                break;
            }
            if (task.status === "failed" || task.status === "partial") {
                throw new Error(`Message ingestion task ${task.status}`);
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        if (!completed) throw new Error("Timed out waiting for message ingestion.");
    }

    console.log("Getting memory for thread");
    const threadMemory = await client.thread.getContext(threadUuid);
    console.log(threadMemory);

    console.log("Getting episodes for user");
    const episodeResult = await client.graph.episode.list(userUuid, { pageSize: 3 });
    const episodes = episodeResult.data;
    console.log(`Episodes for user ${userUuid}:`);
    console.log(episodes);

    if (episodes && episodes.length > 0) {
        const episode = await client.graph.episode.get(userUuid, episodes[0].uuid!);
        console.log(episode);
    }

    const edges = await client.graph.edge.list(userUuid, { pageSize: 10 });
    console.log(`Edges for user ${userUuid}:`);
    console.log(edges.data);

    if (edges.data.length > 0) {
        const edge = await client.graph.edge.get(userUuid, edges.data[0].uuid!);
        console.log(edge);
    }

    const nodes = await client.graph.node.list(userUuid, { pageSize: 10 });
    console.log(`Nodes for user ${userUuid}:`);
    console.log(nodes.data);

    if (nodes.data.length > 0) {
        const node = await client.graph.node.get(userUuid, nodes.data[0].uuid!);
        console.log(node);
    }

    console.log("Searching user graph memory...");
    const graphSearchResults = await client.graph.searchEdges(userUuid, {
        query: "What is the weather in San Francisco?",
        pageSize: 10,
    });
    console.log(graphSearchResults.data);

    console.log("Adding a new text episode to the graph...");
    const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
    const message = "Paul (user): I went to Eric Clapton concert last night";
    const addedEpisodes = await Promise.all([
        client.graph.episode.add(userUuid, {
            type: "text",
            data: "The user is an avid fan of Eric Clapton",
        }),
        client.graph.episode.add(userUuid, { type: "json", data: jsonString }),
        client.graph.episode.add(userUuid, { type: "message", data: message }),
    ]);
    const lastTaskUuid = addedEpisodes.at(-1)?.task?.uuid;
    if (lastTaskUuid) {
        const deadline = Date.now() + 180_000;
        let completed = false;
        while (Date.now() < deadline) {
            const task = await client.task.get(lastTaskUuid);
            if (task.status === "succeeded") {
                completed = true;
                break;
            }
            if (task.status === "failed" || task.status === "partial") {
                throw new Error(`Episode ingestion task ${task.status}`);
            }
            await new Promise((resolve) => setTimeout(resolve, 2000));
        }
        if (!completed) throw new Error("Timed out waiting for episode ingestion.");
    }

    console.log("Getting nodes from the graph...");
    const updatedNodes = await client.graph.node.list(userUuid, { pageSize: 10 });
    console.log(updatedNodes.data);

    console.log("Finding Eric Clapton in the graph...");
    const claptonNode = updatedNodes.data.find(node => node.name === "Eric Clapton");
    console.log(claptonNode);

    if (claptonNode) {
        console.log("Performing Eric Clapton centered edge search...");
        const edgeSearchResults = await client.graph.searchEdges(userUuid, {
            query: "Eric Clapton",
            pageSize: 10,
        });
        console.log(edgeSearchResults.data);

        console.log("Performing Eric Clapton centered node search...");
        const nodeSearchResults = await client.graph.searchNodes(userUuid, {
            query: "Eric Clapton",
            pageSize: 10,
        });
        console.log(nodeSearchResults.data);
    }

    const userNode = nodes.data.find(node => node.labels?.includes("User"));
    if (userNode?.uuid) {
        const userNodeDetails = await client.graph.node.get(userUuid, userNode.uuid);
        console.log("User node: ", userNodeDetails);
        const userCenteredSearch = await client.graph.searchEdges(userUuid, {
            query: "User preferences",
            pageSize: 10,
        });
        console.log("User centered search results", userCenteredSearch.data);
    }

}

main().catch(console.error);