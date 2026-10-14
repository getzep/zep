import { ZepClient } from "@getzep/zep-cloud";

async function main() {
    const projectApiKey = process.env.ZEP_API_KEY;
    if (!projectApiKey) {
        throw new Error("ZEP_API_KEY environment variable is not set");
    }

    const client = new ZepClient({
        apiKey: projectApiKey,
    });

    const createdUserUuids: string[] = [];
    const runId = Date.now();

    // Create multiple users
    for (let i = 0; i < 3; i++) {
        const user = await client.user.create({
            userId: `typescript-users-${runId}-${i}`,
            email: `user${i}-${runId}@example.com`,
            firstName: `John${i}`,
            lastName: `Doe${i}`,
        });
        if (!user.uuid) throw new Error("Zep did not return a user UUID");
        createdUserUuids.push(user.uuid);
        console.log(`Created user ${i + 1}: ${user.uuid}`);
    }

    if (createdUserUuids.length < 2) {
        throw new Error("Need at least two created users to demonstrate update and delete");
    }

    // Update only a user created in this run
    const userUuid = createdUserUuids[0];
    const updatedUser = await client.user.update(userUuid, {
        email: `updated_user+${runId}@example.com`,
        firstName: "UpdatedJohn",
        lastName: "UpdatedDoe",
    });
    console.log(`Updated user: ${updatedUser.uuid}`);

    // Delete only a user created in this run
    const userUuidToDelete = createdUserUuids[1];
    await client.user.delete(userUuidToDelete);
    console.log(`Deleted user: ${userUuidToDelete}`);

    // List a page of users (read-only; do not mutate list results)
    console.log("All users (first page):");
    const usersResult = await client.user.list({ pageSize: 10 });
    for (const listedUser of usersResult.data) {
        console.log(listedUser.uuid);
    }
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
