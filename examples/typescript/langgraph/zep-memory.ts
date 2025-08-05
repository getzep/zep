// zep-memory.ts
import { ZepClient } from "@getzep/zep-cloud";
import { Role } from "@getzep/zep-cloud/dist/api";
import { BaseMessage, AIMessage, HumanMessage, SystemMessage } from "@langchain/core/messages";
import { v4 as uuidv4 } from "uuid";

/**
 * ZepMemory adapter for LangGraph
 * This class provides memory persistence for LangGraph using Zep
 */
export class ZepMemory {
  private client: ZepClient;
  private threadId: string;
  private initialized: boolean = false;
  private userId?: string;

  /**
   * Create a new ZepMemory instance
   * @param apiKey - Zep API key
   * @param threadId - Optional thread ID, will generate a new one if not provided
   * @param userId - Optional user ID to associate with the thread
   */
  constructor(apiKey: string, threadId?: string, userId?: string) {
    this.client = new ZepClient({
      apiKey,
    });
    this.threadId = threadId || uuidv4();
    this.userId = userId;
  }

  /**
   * Initialize the memory thread
   * @param userId - Optional user ID to associate with the thread
   */
  async initialize(userId?: string): Promise<void> {
    if (this.initialized) return;

    try {
      // Use provided userId or the one from constructor or generate a new one
      const userIdToUse = userId || this.userId || `user-${uuidv4()}`;
      this.userId = userIdToUse;

      // Check if user exists, create if not
      let userExists = false;
      try {
        console.log("userIdToUse", userIdToUse);
        await this.client.user.get(userIdToUse);
        userExists = true;
        console.log(`Using existing user: ${userIdToUse}`);
      } catch (error) {
        console.log(error.constructor.name);
        if (error.constructor.name === "NotFoundError") {
          // User doesn't exist, we'll create it
          console.log(`User ${userIdToUse} not found, will create`);
        } else {
          // For other errors, log and rethrow
          console.error(`Error checking if user exists: ${userIdToUse}:`, error);
          throw error;
        }
      }

      // Create user if it doesn't exist
      if (!userExists) {
        try {
          await this.client.user.add({
            userId: userIdToUse,
            firstName: 'Sarah',
            lastName: 'Smith',
            email: `${userIdToUse}@example.com`, // Placeholder email
          });
          console.log(`Created new user: ${userIdToUse}`);
        } catch (error) {
          console.error(`Failed to create user ${userIdToUse}:`, error);
          throw error;
        }
      }

      // Check if thread exists
      let threadExists = false;
      try {
        await this.client.thread.get(this.threadId);
        threadExists = true;
        console.log(`Using existing thread: ${this.threadId}`);
      } catch (error) {
        if (error.constructor.name === "NotFoundError") {
          // Thread doesn't exist, we'll create it
          console.log(`Thread ${this.threadId} not found, will create`);
        } else {
          // For other errors, log and rethrow
          console.error(`Error checking if thread exists ${this.threadId}:`, error);
          throw error;
        }
      }

      // Create thread if it doesn't exist
      if (!threadExists) {
        try {
          await this.client.thread.create({
            threadId: this.threadId,
            userId: userIdToUse,
          });
          console.log(`Created new thread: ${this.threadId}`);
        } catch (error) {
          console.error(`Failed to create thread ${this.threadId}:`, error);
          throw error;
        }
      }

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
      
      // Add message to Zep memory
      await this.client.thread.addMessages(this.threadId, {
        messages: [zepMessage],
      });
      
      let context: string | undefined;
      if (withContext) {
        const contextResponse = await this.client.thread.getUserContext(this.threadId, { mode: "basic" });
        context = contextResponse.context;
      }

      return context;
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
      await this.client.thread.addMessages(this.threadId, {
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
      const response = await this.client.thread.get(this.threadId, {
        limit,
      });

      // Convert Zep messages to LangChain messages
      return (response.messages || []).map(msg => this.convertToLangChainMessage(msg));
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
      const contextResponse = await this.client.thread.getUserContext(this.threadId, { mode: "basic" });
      const messagesResponse = await this.client.thread.get(this.threadId);
      
      // Convert messages to LangChain format
      const messages = (messagesResponse.messages || []).map(msg => this.convertToLangChainMessage(msg));
      
      return {
        messages,
        context: contextResponse.context,
      };
    } catch (error) {
      console.error("Failed to get memory with context:", error);
      throw error;
    }
  }

  /**
   * Get the thread ID
   */
  getThreadId(): string {
    return this.threadId;
  }

  /**
   * Get the user ID
   */
  getUserId(): string | undefined {
    return this.userId;
  }

  /**
   * Convert a LangChain message to a Zep message
   * @param message - LangChain message to convert
   */
  private convertToZepMessage(message: BaseMessage) {
    let role: Role;
    let name = "";

    if (message instanceof AIMessage) {
      role = "assistant" as Role;
    } else if (message instanceof HumanMessage) {
      role = "user" as Role;
    } else if (message instanceof SystemMessage) {
      role = "system" as Role;
    } else {
      // Handle other message types (FunctionMessage, ToolMessage, etc.)
      role = "function" as Role;
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