import { ZepClient, Zep } from "@getzep/zep-cloud";
import {
  AIMessage,
  BaseMessage,
  HumanMessage,
  SystemMessage,
} from "@langchain/core/messages";
import { v4 as uuidv4 } from "uuid";

/**
 * ZepMemory adapter for LangGraph using public @getzep/zep-cloud v3 APIs.
 */
export class ZepMemory {
  private client: ZepClient;
  private threadId: string;
  private initialized: boolean = false;
  private userId?: string;

  constructor(apiKey: string, threadId?: string, userId?: string) {
    this.client = new ZepClient({
      apiKey,
    });
    this.threadId = threadId || uuidv4();
    this.userId = userId;
  }

  async initialize(userId?: string): Promise<void> {
    if (this.initialized) return;

    try {
      const userIdToUse = userId || this.userId || `user-${uuidv4()}`;
      this.userId = userIdToUse;

      let userExists = false;
      try {
        await this.client.user.get(userIdToUse);
        userExists = true;
        console.log(`Using existing user: ${userIdToUse}`);
      } catch (error) {
        if (error instanceof Zep.NotFoundError) {
          console.log(`User ${userIdToUse} not found, will create`);
        } else {
          console.error(`Error checking if user exists: ${userIdToUse}:`, error);
          throw error;
        }
      }

      if (!userExists) {
        await this.client.user.add({
          userId: userIdToUse,
          firstName: "Sarah",
          lastName: "Smith",
          email: `${userIdToUse}@example.com`,
        });
        console.log(`Created new user: ${userIdToUse}`);
      }

      let threadExists = false;
      try {
        await this.client.thread.get(this.threadId);
        threadExists = true;
        console.log(`Using existing thread: ${this.threadId}`);
      } catch (error) {
        if (error instanceof Zep.NotFoundError) {
          console.log(`Thread ${this.threadId} not found, will create`);
        } else {
          console.error(
            `Error checking if thread exists ${this.threadId}:`,
            error,
          );
          throw error;
        }
      }

      if (!threadExists) {
        await this.client.thread.create({
          threadId: this.threadId,
          userId: userIdToUse,
        });
        console.log(`Created new thread: ${this.threadId}`);
      }

      this.initialized = true;
    } catch (error) {
      console.error("Failed to initialize Zep memory:", error);
      throw error;
    }
  }

  async addMessage(
    message: BaseMessage,
    withContext: boolean = false,
  ): Promise<string | undefined> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      const zepMessage = this.convertToZepMessage(message);

      await this.client.thread.addMessages(this.threadId, {
        messages: [zepMessage],
      });

      let context: string | undefined;
      if (withContext) {
        const contextResponse = await this.client.thread.getUserContext(
          this.threadId,
          { mode: "basic" },
        );
        context = contextResponse.context ?? undefined;
      }

      return context;
    } catch (error) {
      console.error("Failed to add message to Zep memory:", error);
      throw error;
    }
  }

  async addMessages(messages: BaseMessage[]): Promise<void> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      const zepMessages = messages.map((msg) => this.convertToZepMessage(msg));

      await this.client.thread.addMessages(this.threadId, {
        messages: zepMessages,
      });
    } catch (error) {
      console.error("Failed to add messages to Zep memory:", error);
      throw error;
    }
  }

  async getMessages(limit: number = 10): Promise<BaseMessage[]> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      const response = await this.client.thread.get(this.threadId, {
        lastn: limit,
      });

      return (response.messages || []).map((msg) =>
        this.convertToLangChainMessage(msg),
      );
    } catch (error) {
      console.error("Failed to get messages from Zep memory:", error);
      throw error;
    }
  }

  async getMemoryWithContext(): Promise<{
    messages: BaseMessage[];
    context?: string;
  }> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      const contextResponse = await this.client.thread.getUserContext(
        this.threadId,
        { mode: "basic" },
      );
      const messagesResponse = await this.client.thread.get(this.threadId);

      const messages = (messagesResponse.messages || []).map((msg) =>
        this.convertToLangChainMessage(msg),
      );

      return {
        messages,
        context: contextResponse.context ?? undefined,
      };
    } catch (error) {
      console.error("Failed to get memory with context:", error);
      throw error;
    }
  }

  getThreadId(): string {
    return this.threadId;
  }

  getUserId(): string | undefined {
    return this.userId;
  }

  private convertToZepMessage(message: BaseMessage): Zep.Message {
    let role: Zep.RoleType;
    let name = "";

    if (message instanceof AIMessage) {
      role = Zep.RoleType.AssistantRole;
    } else if (message instanceof HumanMessage) {
      role = Zep.RoleType.UserRole;
    } else if (message instanceof SystemMessage) {
      role = Zep.RoleType.SystemRole;
    } else {
      role = Zep.RoleType.FunctionRole;
    }

    return {
      content: message.content as string,
      role,
      name,
    };
  }

  private convertToLangChainMessage(message: Zep.Message): BaseMessage {
    const { content, role } = message;

    if (role === Zep.RoleType.AssistantRole || role === "assistant") {
      return new AIMessage(content);
    } else if (role === Zep.RoleType.UserRole || role === "user") {
      return new HumanMessage(content);
    } else if (role === Zep.RoleType.SystemRole || role === "system") {
      return new SystemMessage(content);
    } else {
      return new HumanMessage(content);
    }
  }
}
