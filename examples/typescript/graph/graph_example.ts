import { v4 as uuidv4 } from 'uuid';
import { ZepClient } from '../../src';

const API_KEY = process.env.ZEP_API_KEY;

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    const graphId = uuidv4();
    console.log(`Creating graph ${graphId}...`);
    const graph = await client.graph.create({
        graphId: graphId,
        name: "My graph",
        description: "This is my graph",
    });
    console.log(`graph ${graphId} created`, graph);

    console.log(`Adding episode to graph ${graphId}...`);
    await client.graph.add({
        graphId: graphId,
        type: "text",
        data: "This is a test episode",
    });

    console.log(`Adding more meaningful episode to graph ${graphId}...`);
    await client.graph.add({
        graphId: graphId,
        type: "text",
        data: "Eric Clapton is a rock star",
    });

    console.log(`Adding a JSON episode to graph ${graphId}...`);
    const jsonString = '{"name": "Eric Clapton", "age": 78, "genre": "Rock"}';
    await client.graph.add({
        graphId: graphId,
        type: "json",
        data: jsonString,
    });

    console.log("Waiting for the graph to be updated...");
    await new Promise(resolve => setTimeout(resolve, 10000));

    console.log(`Getting nodes from graph ${graphId}...`);
    const nodes = await client.graph.node.getByGraphId(graphId, {limit: 10});
    console.log(`Nodes from graph ${graphId}`, nodes);

    console.log(`Getting edges from graph ${graphId}...`);
    const edges = await client.graph.edge.getByGraphId(graphId, {limit: 10});
    console.log(`Edges from graph ${graphId}`, edges);

    console.log(`Searching graph ${graphId}...`);
    const searchResults = await client.graph.search({
        graphId: graphId,
        query: "Eric Clapton",
    });
    console.log(`Search results from graph ${graphId}`, searchResults);
}

main().catch(console.error);