import { ZepClient } from "../../src";
import { EntityData, entityFields, EntityType, EdgeType } from "../../src/wrapper/ontology";

const API_KEY = process.env.ZEP_API_KEY;

async function main() {
    const client = new ZepClient({
        apiKey: API_KEY,
    });

    const travelDestinationSchema: EntityType = {
        description: "A travel destination entity",
        fields: {
            destination_name: entityFields.text("The name of travel destination"),
        },
    };

    type TravelDestination = EntityData<typeof travelDestinationSchema>;

    const isTravelingTo: EdgeType = {
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
    }

    await client.graph.setEntityTypes({
        TravelDestination: travelDestinationSchema,
    }, {
        IS_TRAVELING_TO: isTravelingTo,
    });

    const customTypes = await client.graph.listEntityTypes();
    console.log(JSON.stringify(customTypes, null, 2));
}

main().catch(console.error);
