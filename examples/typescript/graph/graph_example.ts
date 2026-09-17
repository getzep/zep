import { ZepClient, Zep } from '@getzep/zep-cloud';

const API_KEY = process.env.ZEP_API_KEY;

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    // v4 gives every graph a server-generated UUID.
    console.log("Creating a graph...");
    const graph = await client.graph.create({
        name: "My graph",
        description: "This is my graph",
    });
    const graphUuid = graph.uuid!;
    console.log(`graph ${graphUuid} created`, graph);

    console.log(`Adding episode to graph ${graphUuid}...`);
    await client.graph.episode.add(graphUuid, {
        type: "text",
        data: "This is a test episode",
    });

    console.log(`Adding more meaningful episode to graph ${graphUuid}...`);
    await client.graph.episode.add(graphUuid, {
        type: "text",
        data: "Eric Clapton is a rock star",
    });

    console.log(`Adding a JSON episode to graph ${graphUuid}...`);
    const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
    await client.graph.episode.add(graphUuid, {
        type: "json",
        data: jsonString,
    });

    console.log("Waiting for the graph to be updated...");
    await new Promise(resolve => setTimeout(resolve, 10000));

    console.log(`Getting nodes from graph ${graphUuid}...`);
    const nodes: Zep.Node[] = [];
    for await (const node of await client.graph.node.list(graphUuid, { limit: 10, body: {} })) {
        nodes.push(node);
        if (nodes.length >= 10) {
            break;
        }
    }
    console.log(`Nodes from graph ${graphUuid}`, nodes);

    console.log(`Getting edges from graph ${graphUuid}...`);
    const edges: Zep.Edge[] = [];
    for await (const edge of await client.graph.edge.list(graphUuid, { limit: 10, body: {} })) {
        edges.push(edge);
        if (edges.length >= 10) {
            break;
        }
    }
    console.log(`Edges from graph ${graphUuid}`, edges);

    console.log(`Searching graph ${graphUuid}...`);
    const searchResults: Zep.Edge[] = [];
    for await (const edge of await client.graph.searchEdges(graphUuid, {
        limit: 10,
        body: {
            query: "Eric Clapton",
        },
    })) {
        searchResults.push(edge);
        if (searchResults.length >= 10) {
            break;
        }
    }
    console.log(`Search results from graph ${graphUuid}`, searchResults);
}

main().catch(console.error);
