import {
    ZepClient,
    Zep,
} from "@getzep/zep-cloud";

const API_KEY = process.env.ZEP_API_KEY;

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    const travelDestinationSchema: Zep.EntityType = {
        name: "TravelDestination",
        description: "A travel destination entity",
        properties: [
            { name: "destination_name", type: "text", description: "The name of the travel destination" },
        ],
    };

    const isTravelingTo: Zep.EdgeType = {
        name: "IS_TRAVELING_TO",
        description: "An edge representing a traveler going to a destination.",
        properties: [
            { name: "travel_date", type: "text", description: "The date of the travel" },
            { name: "purpose", type: "text", description: "The purpose of the travel" },
        ],
        sourceTargets: [
            {
                source: "User",
                target: "TravelDestination",
            }
        ]
    }

    await client.project.setOntology({
        entityTypes: [travelDestinationSchema],
        edgeTypes: [isTravelingTo],
    });

    const ontology = await client.project.getOntology();
    console.log(JSON.stringify(ontology, null, 2));
}

main().catch(console.error);
