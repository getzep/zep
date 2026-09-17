// Command example demonstrates wiring Zep long-term memory into a Google ADK
// for Go agent using the zepadk package.
//
// It creates a Zep user and a Zep thread, keeps the UUIDs that Zep returns,
// and builds an llmagent whose BeforeModelCallback persists each new user turn
// to Zep and injects the context block of the user into the prompt, whose
// AfterModelCallback persists the reply of the assistant back to the same
// thread, registers a graph search tool that the model can call on demand, and
// attaches a Zep memory service at the runner.
//
// Run it with both keys set:
//
//	export ZEP_API_KEY=...      # from https://app.getzep.com
//	export GOOGLE_API_KEY=...   # from https://aistudio.google.com/apikey
//	go run ./examples
//
// With ZEP_API_KEY unset the Zep integration disables itself (the client is
// nil and every Zep call is a no-op) so the agent still runs. This is useful
// to confirm the wiring without a Zep account. With GOOGLE_API_KEY unset the
// program prints the configured wiring and exits before it calls the model.
//
// Zep v4 addresses a user, a thread, and a graph by a server-generated UUID.
// A production application creates the user and the thread one time, stores
// the UUIDs in its own database, and reads them on each turn. This example
// creates them at start and keeps them in memory.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	zepadk "github.com/getzep/zep/integrations/adk/go"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/agent/llmagent"
	"google.golang.org/adk/memory"
	"google.golang.org/adk/model/gemini"
	"google.golang.org/adk/runner"
	"google.golang.org/adk/session"
	"google.golang.org/adk/tool"
	"google.golang.org/genai"
)

const (
	appName   = "zep_adk_example"
	userID    = "user_jane_smith"
	sessionID = "session_demo_001"
	modelName = "gemini-2.5-flash"
)

func main() {
	ctx := context.Background()

	// A nil client (ZEP_API_KEY unset) makes the whole Zep integration a
	// no-op, so this example runs with or without a Zep account.
	zep := zepadk.NewClientFromEnv()

	var userUUID, graphUUID, threadUUID string
	if zep == nil {
		log.Println("ZEP_API_KEY not set: running without Zep memory (all Zep calls are no-ops).")
	} else {
		// Create the Zep user and the Zep thread one time, before the first
		// turn. A real first name, last name, and email help Zep to resolve
		// the identity of the user in the graph. Zep creates a new user on
		// each call, so an application must not call CreateUser on each
		// session start.
		//
		// The user_id label below is a temporary workaround for a defect in
		// the production v4 API: a thread of a user that has no user_id
		// label makes Thread.AddMessages return HTTP 404. The label is a
		// name only. Zep does not use it to address the user, and the
		// package addresses each resource by its UUID.
		zepUserLabel := fmt.Sprintf("zep-adk-go-example-%d", time.Now().UnixNano())
		var err error
		userUUID, graphUUID, err = zepadk.CreateUser(ctx, zep, zepUserLabel, "Jane", "Smith", "jane@example.com")
		if err != nil {
			log.Fatalf("creating Zep user: %v", err)
		}
		log.Printf("Zep user created: user_uuid=%s graph_uuid=%s", userUUID, graphUUID)

		// The thread needs no name. Zep addresses the thread by the UUID
		// that it returns.
		threadUUID, err = zepadk.CreateThread(ctx, zep, "", userUUID)
		if err != nil {
			log.Fatalf("creating Zep thread: %v", err)
		}
		log.Printf("Zep thread created: thread_uuid=%s", threadUUID)
	}

	// Graph search tool that the model can call to recall facts on demand.
	searchTool, err := zepadk.NewGraphSearchTool(zep, zepadk.WithGraphUUID(graphUUID))
	if err != nil {
		log.Fatalf("building graph search tool: %v", err)
	}

	// BeforeModelCallback persists each user turn and injects the context
	// block.
	beforeModel := zepadk.NewBeforeModelCallback(zep,
		zepadk.WithThreadUUID(threadUUID),
		zepadk.WithUserUUID(userUUID),
		zepadk.WithUserMessageName("Jane"))

	// AfterModelCallback persists the reply of the assistant, so the graph of
	// the user receives both halves of the conversation.
	afterModel := zepadk.NewAfterModelCallback(zep,
		zepadk.WithAfterThreadUUID(threadUUID),
		zepadk.WithAssistantMessageName("assistant"))

	// Without a Google API key we cannot construct the model. Print the
	// wiring that we would use and exit cleanly.
	if os.Getenv("GOOGLE_API_KEY") == "" {
		fmt.Println("GOOGLE_API_KEY not set: agent + runner wiring is configured but the model will not be called.")
		fmt.Printf("Configured: app=%q user=%q session=%q model=%q tools=[%s]\n",
			appName, userID, sessionID, modelName, searchTool.Name())
		return
	}

	llm, err := gemini.NewModel(ctx, modelName, &genai.ClientConfig{
		APIKey:  os.Getenv("GOOGLE_API_KEY"),
		Backend: genai.BackendGeminiAPI,
	})
	if err != nil {
		log.Fatalf("creating Gemini model: %v", err)
	}

	zepAgent, err := llmagent.New(llmagent.Config{
		Name:        "zep_memory_agent",
		Description: "A helpful assistant with long-term memory backed by Zep.",
		Model:       llm,
		Instruction: "You are a helpful assistant with long-term memory. " +
			"Use the search_memory tool to recall details about the user when relevant.",
		BeforeModelCallbacks: []llmagent.BeforeModelCallback{beforeModel},
		AfterModelCallbacks:  []llmagent.AfterModelCallback{afterModel},
		Tools:                []tool.Tool{searchTool},
	})
	if err != nil {
		log.Fatalf("creating agent: %v", err)
	}

	sessions := session.InMemoryService()
	if _, err := sessions.Create(ctx, &session.CreateRequest{
		AppName:   appName,
		UserID:    userID,
		SessionID: sessionID,
	}); err != nil {
		log.Fatalf("creating session: %v", err)
	}

	// Zep memory service: the built-in memory tooling of ADK reaches it
	// through ToolContext.SearchMemory.
	memories := zepadk.NewMemoryService(zep, zepadk.WithMemoryGraphUUID(graphUUID))

	run, err := runner.New(runner.Config{
		AppName:        appName,
		Agent:          zepAgent,
		SessionService: sessions,
		MemoryService:  memories,
	})
	if err != nil {
		log.Fatalf("creating runner: %v", err)
	}

	// The first turn tells the agent two facts about the user.
	fmt.Printf("\n>>> %s\n", "Hi! My name is Jane and I'm a vegetarian who loves hiking.")
	send(ctx, run, "Hi! My name is Jane and I'm a vegetarian who loves hiking.")

	// Zep ingestion is asynchronous, so the facts of the first turn are not
	// immediately retrievable. Poll the graph until the first fact appears.
	if zep != nil {
		waitForIngestion(ctx, memories, graphUUID)
	}

	// The second turn requires the agent to recall the facts of the first
	// turn from Zep.
	fmt.Printf("\n>>> %s\n", "Can you suggest a meal for after my next hike?")
	send(ctx, run, "Can you suggest a meal for after my next hike?")
}

// waitForIngestion polls the Zep graph until it returns at least one memory
// for the query, or until the time limit expires. Zep ingestion is
// asynchronous: a fact that the agent adds during a turn is not immediately
// retrievable.
func waitForIngestion(ctx context.Context, memories memory.Service, graphUUID string) {
	if graphUUID == "" || memories == nil {
		return
	}
	deadline := time.Now().Add(2 * time.Minute)
	for time.Now().Before(deadline) {
		res, err := memories.SearchMemory(ctx, &memory.SearchRequest{
			AppName: appName,
			UserID:  userID,
			Query:   "What does Jane eat?",
		})
		if err == nil && res != nil && len(res.Memories) > 0 {
			fmt.Printf("\nZep ingestion complete: %d fact(s) available.\n", len(res.Memories))
			return
		}
		time.Sleep(5 * time.Second)
	}
	fmt.Println("\nZep ingestion did not complete within the time limit; the next turn may have no memory.")
}

// send streams one user turn through the runner and prints the final reply.
func send(ctx context.Context, run *runner.Runner, prompt string) {
	msg := genai.NewContentFromText(prompt, genai.RoleUser)
	for event, err := range run.Run(ctx, userID, sessionID, msg, agent.RunConfig{}) {
		if err != nil {
			log.Printf("run error: %v", err)
			return
		}
		if event != nil && event.IsFinalResponse() && event.Content != nil {
			fmt.Printf("<<< %s\n", zepadk.AssistantText(event.Content))
		}
	}
}
