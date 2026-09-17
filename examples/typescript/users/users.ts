import { ZepClient, Zep } from "@getzep/zep-cloud";

async function main() {
    const projectApiKey = process.env.ZEP_API_KEY;
    if (!projectApiKey) {
        throw new Error("ZEP_API_KEY environment variable is not set");
    }

    const client = new ZepClient({
        apiKey: projectApiKey,
    });

    const createdUserUuids: string[] = [];

    // Create multiple users. v4 gives every user a server-generated UUID, so a
    // create call does not send a userId.
    for (let i = 0; i < 3; i++) {
        const userRequest: Zep.CreateUserRequest = {
            email: `user${i}@example.com`,
            firstName: `John${i}`,
            lastName: `Doe${i}`,
        };

        const user = await client.user.create(userRequest);
        if (!user.uuid) {
            throw new Error("The server did not return a user UUID");
        }
        createdUserUuids.push(user.uuid);
        console.log(`Created user ${i + 1}: ${user.uuid}`);
    }

    if (createdUserUuids.length < 2) {
        throw new Error("Need at least two created users to demonstrate update and delete");
    }

    // Update only a user created in this run
    const userUuid = createdUserUuids[0];
    const userRequest: Zep.PatchUserRequest = {
        email: "updated_user@example.com",
        firstName: "UpdatedJohn",
        lastName: "UpdatedDoe",
    };
    const updatedUser = await client.user.update(userUuid, userRequest);
    console.log(`Updated user: ${updatedUser.uuid}`);

    // Delete only a user created in this run. The delete is asynchronous.
    const userUuidToDelete = createdUserUuids[1];
    await client.user.delete(userUuidToDelete);
    console.log(`Deleted user: ${userUuidToDelete}`);

    // List a page of users (read-only; do not mutate list results)
    console.log("All users (first page):");
    let count = 0;
    for await (const user of await client.user.list({ limit: 10 })) {
        console.log(user.uuid);
        count += 1;
        if (count >= 10) {
            break;
        }
    }
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
