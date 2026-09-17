import { ZepClient, Zep } from '@getzep/zep-cloud';
import { history } from './conversations';

const API_KEY = process.env.ZEP_API_KEY

async function collect<T>(pager: AsyncIterable<T>, limit: number): Promise<T[]> {
    const items: T[] = [];
    for await (const item of pager) {
        items.push(item);
        if (items.length >= limit) {
            break;
        }
    }
    return items;
}

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    // Create a user. v4 gives every user a server-generated UUID and a graph
    // UUID.
    const userRequest: Zep.CreateUserRequest = {
        firstName: 'Paul',
    };
    const user = await client.user.create(userRequest);
    const userUuid = user.uuid!;
    const graphUuid = user.graphUuid!;
    console.log(`User ${userUuid} created`);

    // Create a thread
    const thread = await client.thread.create({
        userUuid: userUuid,
    });
    const threadUuid = thread.uuid!;
    console.log(`thread ${threadUuid} created`);

    // Add messages to the thread
    for (const message of history[2]) {
        await client.thread.addMessages(threadUuid, {
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

    console.log("Getting the context for the thread");
    const threadContext = await client.thread.getContext(threadUuid);
    console.log(threadContext);

    console.log("Getting episodes for the user graph");
    const episodes = await collect(await client.graph.episode.list(graphUuid, { limit: 3, body: {} }), 3);
    console.log(`Episodes for graph ${graphUuid}:`);
    console.log(episodes);

    if (episodes.length > 0) {
        const episode = await client.graph.episode.get(graphUuid, episodes[0].uuid!);
        console.log(episode);
    }

    const edges = await collect(await client.graph.edge.list(graphUuid, { limit: 10, body: {} }), 10);
    console.log(`Edges for graph ${graphUuid}:`);
    console.log(edges);

    if (edges.length > 0) {
        const edge = await client.graph.edge.get(graphUuid, edges[0].uuid!);
        console.log(edge);
    }

    const nodes = await collect(await client.graph.node.list(graphUuid, { limit: 10, body: {} }), 10);
    console.log(`Nodes for graph ${graphUuid}:`);
    console.log(nodes);

    if (nodes.length > 0) {
        const node = await client.graph.node.get(graphUuid, nodes[0].uuid!);
        console.log(node);
    }

    console.log("Searching the user graph...");
    const graphSearchResults = await collect(
        await client.graph.searchEdges(graphUuid, {
            limit: 10,
            body: {
                query: "What is the weather in San Francisco?",
            },
        }),
        10,
    );
    console.log(graphSearchResults);

    console.log("Adding a new text episode to the graph...");
    await client.graph.episode.add(graphUuid, {
        type: "text",
        data: "The user is an avid fan of Eric Clapton",
    });
    console.log("Text episode added");

    console.log("Adding a new JSON episode to the graph...");
    const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
    await client.graph.episode.add(graphUuid, {
        type: "json",
        data: jsonString,
    });
    console.log("JSON episode added");

    console.log("Adding a new message episode to the graph...");
    const message = "Paul (user): I went to Eric Clapton concert last night";
    await client.graph.episode.add(graphUuid, {
        type: "message",
        data: message,
    });
    console.log("Message episode added");

    console.log("Waiting for the graph to be updated...");
    await new Promise(resolve => setTimeout(resolve, 30000));

    console.log("Getting nodes from the graph...");
    const updatedNodes = await collect(await client.graph.node.list(graphUuid, { limit: 10, body: {} }), 10);
    console.log(updatedNodes);

    console.log("Finding Eric Clapton in the graph...");
    const claptonNode = updatedNodes.find(node => node.name === "Eric Clapton");
    console.log(claptonNode);

    if (claptonNode) {
        console.log("Performing Eric Clapton centered edge search...");
        const edgeSearchResults = await collect(
            await client.graph.searchEdges(graphUuid, {
                limit: 10,
                body: {
                    query: "Eric Clapton",
                    centerNodeUuid: claptonNode.uuid,
                },
            }),
            10,
        );
        console.log(edgeSearchResults);

        console.log("Performing Eric Clapton centered node search...");
        const nodeSearchResults = await collect(
            await client.graph.searchNodes(graphUuid, {
                limit: 10,
                body: {
                    query: "Eric Clapton",
                    centerNodeUuid: claptonNode.uuid,
                },
            }),
            10,
        );
        console.log(nodeSearchResults);
    }

    const userNode = await client.user.getNode(userUuid);
    if (userNode) {
        console.log("User node: ", userNode)
        const userCenteredSearch = await collect(
            await client.graph.searchEdges(graphUuid, {
                limit: 10,
                body: {
                    query: "User preferences",
                    centerNodeUuid: userNode.uuid,
                    reranker: "node_distance",
                },
            }),
            10,
        );
        console.log("User centered search results", userCenteredSearch)
    }

}

main().catch(console.error);
