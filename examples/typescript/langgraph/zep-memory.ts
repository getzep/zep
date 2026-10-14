// zep-memory.ts
import { ZepClient, Zep } from "@getzep/zep-cloud";
import { BaseMessage, AIMessage, HumanMessage, SystemMessage } from "@langchain/core/messages";
import { v4 as uuidv4 } from "uuid";

/**
 * ZepMemory adapter for LangGraph
 * This class provides memory persistence for LangGraph using Zep
 */
export class ZepMemory {
  private client: ZepClient;
  private threadUuid: string;
  private initialized: boolean = false;
  private userUuid?: string;

  /**
   * Create a new ZepMemory instance
   * @param apiKey - Zep API key
   * @param threadUuid - Optional Zep thread UUID
   * @param userUuid - Optional Zep user UUID
   */
  constructor(apiKey: string, threadUuid?: string, userUuid?: string) {
    this.client = new ZepClient({
      apiKey,
    });
    this.threadUuid = threadUuid || "";
    this.userUuid = userUuid;
  }

  /**
   * Initialize the memory thread
   * Create a user and thread when their UUIDs are not provided.
   */
  async initialize(): Promise<void> {
    if (this.initialized) return;

    try {
      if (Boolean(this.threadUuid) !== Boolean(this.userUuid)) {
        throw new Error("Provide both --thread-uuid and --user-uuid to reuse a conversation.");
      }

      if (!this.userUuid) {
        const user = await this.client.user.create({
          userId: `langgraph-${uuidv4()}`,
          firstName: "Sarah",
          lastName: "Smith",
          email: `langgraph-${uuidv4()}@example.com`,
        });
        this.userUuid = user.uuid;
      }

      if (!this.userUuid) throw new Error("Zep did not return a user UUID");

      if (!this.threadUuid) {
        const thread = await this.client.thread.create({ userUuid: this.userUuid });
        this.threadUuid = thread.uuid;
      }
      if (!this.threadUuid) throw new Error("Zep did not return a thread UUID");

      this.initialized = true;
    } catch (error) {
      console.error("Failed to initialize Zep memory:", error);
      throw error;
    }
  }

  /**
   * Add a message to memory
   * @param message - LangChain message to add
   * @param withContext - Whether to return the Zep context string from memory
   */
  async addMessage(message: BaseMessage, withContext: boolean = false): Promise<string | undefined> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      // Convert LangChain message to Zep message format
      const zepMessage = this.convertToZepMessage(message);
      
      const response = await this.client.thread.addMessages(this.threadUuid, {
        messages: [zepMessage],
        ...(withContext ? { returnContext: true } : {}),
      });
      return withContext ? response.context : undefined;
    } catch (error) {
      console.error("Failed to add message to Zep memory:", error);
      throw error;
    }
  }

  /**
   * Add multiple messages to memory
   * @param messages - Array of LangChain messages to add
   */
  async addMessages(messages: BaseMessage[]): Promise<void> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      // Convert LangChain messages to Zep message format
      const zepMessages = messages.map(msg => this.convertToZepMessage(msg));
      
      // Add messages to Zep memory
      await this.client.thread.addMessages(this.threadUuid, {
        messages: zepMessages,
      });
    } catch (error) {
      console.error("Failed to add messages to Zep memory:", error);
      throw error;
    }
  }

  /**
   * Get messages from memory
   * @param limit - Maximum number of messages to retrieve
   */
  async getMessages(limit: number = 10): Promise<BaseMessage[]> {
    if (!this.initialized) {
      throw new Error("Memory not initialized");
    }

    try {
      const response = await this.client.thread.listMessages(this.threadUuid, {
        pageSize: limit,
      });

      // Convert Zep messages to LangChain messages
      return response.data.map(msg => this.convertToLangChainMessage(msg));
    } catch (error) {
      console.error("Failed to get messages from Zep memory:", error);
      throw error;
    }
  }

  /**
   * Get memory with context for the current session
   * This retrieves messages along with any context
   */
  async getMemoryWithContext(): Promise<{ messages: BaseMessage[], context?: string }> {
    if (!this.initialized) {
        throw new Error("Memory not initialized");
    }

    try {
      const contextResponse = await this.client.thread.getContext(this.threadUuid);
      const messagesResponse = await this.client.thread.listMessages(this.threadUuid);
      
      // Convert messages to LangChain format
      const messages = messagesResponse.data.map(msg => this.convertToLangChainMessage(msg));
      
      return {
        messages,
        context: contextResponse.context,
      };
    } catch (error) {
      console.error("Failed to get memory with context:", error);
      throw error;
    }
  }

  /** Get the Zep thread UUID. */
  getThreadUuid(): string {
    return this.threadUuid;
  }

  /** Get the Zep user UUID. */
  getUserUuid(): string | undefined {
    return this.userUuid;
  }

  /**
   * Convert a LangChain message to a Zep message
   * @param message - LangChain message to convert
   */
  private convertToZepMessage(message: BaseMessage) {
    let role: Zep.RoleType;
    let name = "";

    if (message instanceof AIMessage) {
      role = "assistant" as Zep.RoleType;
    } else if (message instanceof HumanMessage) {
      role = "user" as Zep.RoleType;
    } else if (message instanceof SystemMessage) {
      role = "system" as Zep.RoleType;
    } else {
      // Handle other message types (FunctionMessage, ToolMessage, etc.)
      role = "function" as Zep.RoleType;
    }

    return {
      content: message.content as string,
      role,
      name,
    };
  }

  /**
   * Convert a Zep message to a LangChain message
   * @param message - Zep message to convert
   */
  private convertToLangChainMessage(message: any): BaseMessage {
    const { content, role } = message;

    if (role === "assistant") {
      return new AIMessage(content);
    } else if (role === "user") {
      return new HumanMessage(content);
    } else if (role === "system") {
      return new SystemMessage(content);
    } else {
      // Default to HumanMessage for other types
      return new HumanMessage(content);
    }
  }
} 