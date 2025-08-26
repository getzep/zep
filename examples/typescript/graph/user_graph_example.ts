import { v4 as uuidv4 } from 'uuid';
import { ZepClient } from '../../src';
import { CreateUserRequest } from '../../src/api';

// @ts-ignore
import { history } from './conversations';

const API_KEY = process.env.ZEP_API_KEY

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    const userId = uuidv4();
    const threadId = uuidv4();

    // Create a user
    const userRequest: CreateUserRequest = {
        userId: userId,
        firstName: 'Paul',
    };
    await client.user.add(userRequest);
    console.log(`User ${userId} created`);

    // Create a thread
    await client.thread.create({
        threadId: threadId,
        userId: userId,
    });
    console.log(`thread ${threadId} created`);

    // Add messages to the thread
    for (const message of history[2]) {
        await client.thread.addMessages(threadId, {
            messages: [
                {
                    role: message.role,
                    name: message.name,
                    content: message.content,
                },
            ],
        });
    }

    console.log("Waiting for the graph to be updated...");
    await new Promise(resolve => setTimeout(resolve, 10000));

    console.log("Getting memory for thread");
    const threadMemory = await client.thread.getUserContext(threadId);
    console.log(threadMemory);

    console.log("Getting episodes for user");
    const episodeResult = await client.graph.episode.getByUserId(userId, { lastn: 3 });
    const episodes = episodeResult.episodes;
    console.log(`Episodes for user ${userId}:`);
    console.log(episodes);

    if (episodes && episodes.length > 0) {
        const episode = await client.graph.episode.get(episodes[0].uuid!);
        console.log(episode);
    }

    const edges = await client.graph.edge.getByUserId(userId, { limit: 10 });
    console.log(`Edges for user ${userId}:`);
    console.log(edges);

    if (edges && edges.length > 0) {
        const edge = await client.graph.edge.get(edges[0].uuid);
        console.log(edge);
    }

    const nodes = await client.graph.node.getByUserId(userId, { limit: 10 });
    console.log(`Nodes for user ${userId}:`);
    console.log(nodes);

    if (nodes && nodes.length > 0) {
        const node = await client.graph.node.get(nodes[0].uuid);
        console.log(node);
    }

    console.log("Searching user graph memory...");
    const graphSearchResults = await client.graph.search({
        userId: userId,
        query: "What is the weather in San Francisco?",
    });
    console.log(graphSearchResults.edges);

    console.log("Adding a new text episode to the graph...");
    await client.graph.add({
        userId: userId,
        type: "text",
        data: "The user is an avid fan of Eric Clapton",
    });
    console.log("Text episode added");

    console.log("Adding a new JSON episode to the graph...");
    const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
    await client.graph.add({
        userId: userId,
        type: "json",
        data: jsonString,
    });
    console.log("JSON episode added");

    console.log("Adding a new message episode to the graph...");
    const message = "Paul (user): I went to Eric Clapton concert last night";
    await client.graph.add({
        userId: userId,
        type: "message",
        data: message,
    });
    console.log("Message episode added");

    console.log("Waiting for the graph to be updated...");
    await new Promise(resolve => setTimeout(resolve, 30000));

    console.log("Getting nodes from the graph...");
    const updatedNodes = await client.graph.node.getByUserId(userId, { limit: 10 });
    console.log(updatedNodes);

    console.log("Finding Eric Clapton in the graph...");
    const claptonNode = updatedNodes.find(node => node.name === "Eric Clapton");
    console.log(claptonNode);

    if (claptonNode) {
        console.log("Performing Eric Clapton centered edge search...");
        const edgeSearchResults = await client.graph.search({
            userId: userId,
            query: "Eric Clapton",
            centerNodeUuid: claptonNode.uuid,
            scope: "edges",
        });
        console.log(edgeSearchResults.edges);

        console.log("Performing Eric Clapton centered node search...");
        const nodeSearchResults = await client.graph.search({
            userId: userId,
            query: "Eric Clapton",
            centerNodeUuid: claptonNode.uuid,
            scope: "nodes",
        });
        console.log(nodeSearchResults.nodes);
    }

    const {node: userNode} = await client.user.getNode(userId)
    if (userNode) {
        console.log("User node: ", userNode)
        const userCenteredSearch = await client.graph.search({
            userId,
            query: "User preferences",
            centerNodeUuid: userNode.uuid,
            reranker: "node_distance",
        })
        console.log("User centered search results", userCenteredSearch.edges)
    }

}

main().catch(console.error);