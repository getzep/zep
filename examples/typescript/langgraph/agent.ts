// agent.ts
import dotenv from "dotenv";

// Load environment variables from .env file
dotenv.config();

import { TavilySearchResults } from "@langchain/community/tools/tavily_search";
import { ChatOpenAI } from "@langchain/openai";
import { HumanMessage, AIMessage, BaseMessage, SystemMessage } from "@langchain/core/messages";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { StateGraph, MessagesAnnotation } from "@langchain/langgraph";
import { fileURLToPath } from "url";
import path from "path";
import { Command } from "commander";
import { ZepMemory } from "./zep-memory";

// Define the tools for the agent to use
const tools = [new TavilySearchResults({ maxResults: 3 })];
const toolNode = new ToolNode(tools);

// Create a model and give it access to the tools
const model = new ChatOpenAI({
  model: "gpt-4o-mini",
  temperature: 0,
}).bindTools(tools);

// Define the function that determines whether to continue or not
function shouldContinue({ messages }: typeof MessagesAnnotation.State) {
  const lastMessage = messages[messages.length - 1] as AIMessage;

  // If the LLM makes a tool call, then we route to the "tools" node
  if (lastMessage.tool_calls?.length) {
    return "tools";
  }
  // Otherwise, we stop (reply to the user) using the special "__end__" node
  return "__end__";
}

// Define the function that calls the model
async function callModel(state: typeof MessagesAnnotation.State) {
  const response = await model.invoke(state.messages);
  return { messages: [...state.messages, response] };
}

// Define the workflow as a graph
const workflow = new StateGraph(MessagesAnnotation)
  .addNode("agent", callModel)
  .addNode("tools", toolNode)
  .addEdge("__start__", "agent")
  .addConditionalEdges("agent", shouldContinue, ["tools", "__end__"])
  .addEdge("tools", "agent");

// Compile the graph
export const graph = workflow.compile();

// Parse command line arguments using Commander
function parseCommandLineArgs() {
  const program = new Command();
  
  program
    .name("langgraph-agent")
    .description("LangGraph CLI Agent with Zep memory integration")
    .version("1.0.0")
    .option("--userId <id>", "User ID to associate with the conversation")
    .option("--user-id <id>", "User ID to associate with the conversation (alternative format)")
    .option("--threadId <id>", "Thread ID for the conversation")
    .option("--thread-id <id>", "Thread ID for the conversation (alternative format)")
    .option("--system-message <message>", "Custom system message to use")
    .option("--debug", "Enable debug mode with additional logging");
  
  program.parse();
  
  const options = program.opts();
  
  // Handle alternative formats and naming
  return {
    userId: options.userId,
    threadId: options.threadId,
    systemMessage: options.systemMessage || "You are a helpful assistant. Answer the user's questions to the best of your ability.",
    debug: !!options.debug
  };
}

// Check if this file is being run directly
const isMainModule = () => {
  if (typeof require !== 'undefined' && require.main === module) {
    return true;
  }
  
  if (import.meta.url) {
    try {
      const currentFilePath = fileURLToPath(import.meta.url);
      const currentFileName = path.basename(currentFilePath);
      // Check if this script was run directly with node/tsx
      return process.argv[1] && process.argv[1].endsWith(currentFileName);
    } catch (e) {
      return false;
    }
  }
  
  return false;
};

// Parse command line arguments once and store the result
const args = isMainModule() ? parseCommandLineArgs() : {
  userId: undefined,
  threadId: undefined,
  systemMessage: "You are a helpful assistant. Answer the user's questions to the best of your ability.",
  debug: false
};

// Initialize Zep memory if API key is available
let zepMemory: ZepMemory | undefined;
if (process.env.ZEP_API_KEY) {
  zepMemory = new ZepMemory(process.env.ZEP_API_KEY, args.threadId, args.userId);
  
  if (args.debug) {
    console.log("Zep memory initialized with thread ID:", zepMemory.getThreadId());
    if (args.userId) {
      console.log("Using user ID:", args.userId);
    }
  }
}

// CLI interface
if (isMainModule()) {
  // This will run when the script is executed directly
  const runCLI = async () => {
    const systemMessage = args.systemMessage;
    
    console.log("🦜🔗 LangGraph Agent CLI");
    if (args.userId) {
      console.log(`User ID: ${args.userId}`);
    }
    if (args.threadId) {
      console.log(`Thread ID: ${args.threadId}`);
    }
    console.log("Type 'exit' to quit the application");
    console.log("------------------------------");
    
    // Create a readline interface
    const readline = await import("readline");
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    
    // Create a new chat session
    const config = { configurable: { sessionId: args.threadId || "cli-session" } };
    let state = { messages: [] as BaseMessage[] };
    
    // Initialize Zep memory if available
    if (zepMemory) {
      try {
        await zepMemory.initialize(args.userId);
        console.log("Connected to Zep memory service");
        
        // Try to load previous messages from Zep memory
        try {
          const previousMessages = await zepMemory.getMessages(10);
          if (previousMessages.length > 0) {
            state.messages = previousMessages;
            console.log(`Loaded ${previousMessages.length} messages from previous conversation`);
          }
        } catch (error) {
          console.error("Failed to load previous messages:", error);
          console.log("Starting a new conversation");
        }
      } catch (error) {
        console.error("Failed to initialize Zep memory:", error);
        console.log("Continuing without memory persistence");
        zepMemory = undefined;
      }
    }
    
    const askQuestion = () => {
      rl.question("\nYou: ", async (input) => {
        if (input.toLowerCase() === "exit") {
          console.log("Goodbye!");
          rl.close();
          return;
        }
        
        // Add the user's message to the state
        const userMessage = new HumanMessage(input);
        let systemMessageWithContext = systemMessage;
        
        // Persist user message to Zep memory if available
        let context: string | undefined;
        if (zepMemory) {
          try {
            // Add the user message to Zep memory and get the context, if available
            context = await zepMemory.addMessage(userMessage, true);
          } catch (error) {
            console.error("Failed to persist user message to Zep memory:", error);
          }
          
          try {
            if (args.debug) {
              console.log(context);
            }
            if (context) {
              systemMessageWithContext = systemMessage + "\n" + context;
            }
          } catch (error) {
            console.error("Failed to get memory with context:", error);
          }
        }
        
        // Get the last 5 messages from the current state (excluding any previous system messages)
        const previousMessages = state.messages
          .filter(msg => !(msg instanceof SystemMessage))
          .slice(-5);
        
        // Create new state with system message at the beginning followed by the history messages
        state.messages = [
          new SystemMessage(systemMessageWithContext), 
          ...previousMessages, 
          userMessage
        ];
        
        try {
          // Process the input through the graph
          const result = await graph.invoke(state, config);
          
          // Extract the AI's response using instanceof instead of _getType()
          const aiMessages = result.messages.filter((msg) => msg instanceof AIMessage);
          const lastAIMessage = aiMessages[aiMessages.length - 1] as AIMessage;
          
          console.log(`\nAI: ${lastAIMessage.content}`);
          
          // Persist AI message to Zep memory if available
          if (zepMemory) {
            try {
              await zepMemory.addMessage(lastAIMessage);
            } catch (error) {
              console.error("Failed to persist AI message to Zep memory:", error);
            }
          }
          
          // Update the state for the next interaction
          // We don't want to completely replace the state, just update the messages
          // to maintain our structure with the system message at the beginning
          const resultMessages = result.messages.filter(msg => !(msg instanceof SystemMessage));
          state = { 
            messages: resultMessages 
          };
        } catch (error) {
          console.error("Error:", error);
        }
        
        // Ask for the next input
        askQuestion();
      });
    };
    
    askQuestion();
  };
  
  runCLI().catch(console.error);
}
