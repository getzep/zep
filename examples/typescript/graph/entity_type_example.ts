import {
    ZepClient,
    buildOntology,
    entityFields,
    type EntityData,
} from "@getzep/zep-cloud";

const API_KEY = process.env.ZEP_API_KEY;

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    const travelDestinationSchema = {
        description: "A travel destination entity",
        fields: {
            destination_name: entityFields.text("The name of travel destination"),
        },
    } as const;

    type TravelDestination = EntityData<typeof travelDestinationSchema>;

    const isTravelingTo = {
        description: "An edge representing a traveler going to a destination.",
        fields: {
            travel_date: entityFields.text("The date of the travel"),
            purpose: entityFields.text("The purpose of the travel"),
        },
        sourceTargets: [
            {
                source: "User",
                target: "TravelDestination",
            }
        ]
    } as const;

    const ontology = buildOntology({
        entities: { TravelDestination: travelDestinationSchema },
        edges: { IS_TRAVELING_TO: isTravelingTo },
    });

    // v4 sets an ontology for one scope at a time. The project scope applies
    // the ontology to every graph of the project.
    await client.project.setOntology(ontology);

    const customTypes = await client.project.getOntology();
    console.log(JSON.stringify(customTypes, null, 2));
}

main().catch(console.error);
