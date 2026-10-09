import { ZepClient, Zep } from "@getzep/zep-cloud";
import { history } from "./chat_shoe_store_history";

function sleep(ms: number) {
    const date = Date.now();
    let currentDate = 0;
    do {
        currentDate = Date.now();
    } while (currentDate - date < ms);
}

async function main() {
    const projectApiKey = process.env.ZEP_API_KEY;

    const client = new ZepClient({
        apiKey: projectApiKey,
    });

    // Create a user. v4 gives every user a server-generated UUID.
    const userRequest: Zep.CreateUserRequest = {
        metadata: { role: "admin" },
        email: "amy@acme.com",
        firstName: "Amy",
        lastName: "Wu",
    };
    const user = await client.user.create(userRequest);
    console.debug("Created user ", user);

    // Add a thread that belongs to the user above. The server generates the
    // thread UUID.
    const thread = await client.thread.create({
        userUuid: user.uuid!,
    });
    const threadUuid = thread.uuid!;
    console.debug("Added new thread ", threadUuid);

    // Get thread
    try {
        const retrievedThread = await client.thread.get(threadUuid);
        console.debug("Retrieved thread ", retrievedThread);
    } catch (error) {
        console.debug("Got error:", error);
    }

    // Add memory. We could do this in a batch, but we'll do it one by one rather to
    // ensure that summaries and other artifacts are generated correctly.
    try {
        for (const { role, name, content } of history) {
            await client.thread.addMessages(threadUuid, {
                messages: [{ role, name, content }],
            });
        }
        console.debug("Added new messages for thread ", threadUuid);
    } catch (error) {
        console.debug("Got error:", error);
    }

    console.log("Sleeping for 5 seconds to let background tasks complete...");
    sleep(5000); // Sleep for 5 seconds
    console.log("Done sleeping!");

    // Get newly added memory
    try {
        console.debug("Getting the context for the thread ", threadUuid);
        const memory = await client.thread.getContext(threadUuid);
        console.log("Context: ", memory.context);
        if (memory.context) {
            console.debug("Memory Context: ", memory.context);
        }
    } catch (error) {
        if (error instanceof Zep.NotFoundError) {
            console.error("thread not found:", error.message);
        } else {
            console.error("Got error:", error);
        }
    }

    // get thread messages
    try {
        const messages: Zep.Message[] = [];
        for await (const message of await client.thread.listMessages(threadUuid, { limit: 10 })) {
            messages.push(message);
            if (messages.length >= 10) {
                break;
            }
        }
        console.debug("thread messages: ", JSON.stringify(messages));
    } catch (error) {
        if (error instanceof Zep.NotFoundError) {
            console.error("thread not found:", error.message);
        } else {
            console.error("Got error:", error);
        }
    }
}

main();
