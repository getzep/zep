import { v4 as uuidv4 } from "uuid";
import { ZepClient, Zep } from "@getzep/zep-cloud";

async function main() {
    const projectApiKey = process.env.ZEP_API_KEY;
    if (!projectApiKey) {
        throw new Error("ZEP_API_KEY environment variable is not set");
    }

    const client = new ZepClient({
        apiKey: projectApiKey,
    });

    const createdUserIds: string[] = [];

    // Create multiple users
    for (let i = 0; i < 3; i++) {
        const userId = uuidv4();
        const userRequest: Zep.CreateUserRequest = {
            userId: userId,
            email: `user${i}@example.com`,
            firstName: `John${i}`,
            lastName: `Doe${i}`,
        };

        const user = await client.user.add(userRequest);
        const createdId = user.userId ?? userId;
        createdUserIds.push(createdId);
        console.log(`Created user ${i + 1}: ${createdId}`);
    }

    if (createdUserIds.length < 2) {
        throw new Error("Need at least two created users to demonstrate update and delete");
    }

    // Update only a user created in this run
    const userId = createdUserIds[0];
    const userRequest: Zep.UpdateUserRequest = {
        email: "updated_user@example.com",
        firstName: "UpdatedJohn",
        lastName: "UpdatedDoe",
    };
    const updatedUser = await client.user.update(userId, userRequest);
    console.log(`Updated user: ${updatedUser.userId}`);

    // Delete only a user created in this run
    const userIdToDelete = createdUserIds[1];
    await client.user.delete(userIdToDelete);
    console.log(`Deleted user: ${userIdToDelete}`);

    // List a page of users (read-only; do not mutate list results)
    console.log("All users (first page):");
    const usersResult = await client.user.listOrdered({ pageSize: 10, pageNumber: 1 });
    if (usersResult && usersResult.users) {
        for (const user of usersResult.users) {
            console.log(user.userId);
        }
    }
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});
