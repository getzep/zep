// Command example demonstrates wiring Zep long-term memory into a Google ADK
// for Go agent using the zepadk package.
//
// It builds an llmagent whose BeforeModelCallback persists each user turn to
// Zep and injects the user's Zep Context Block into the prompt, registers a
// graph-search tool the model can call on demand, and attaches a Zep-backed
// memory.Service at the runner.
//
// Run it with both keys set:
//
//	export ZEP_API_KEY=...      # from https://app.getzep.com
//	export GOOGLE_API_KEY=...   # from https://aistudio.google.com/apikey
//	go run ./examples
//
// With ZEP_API_KEY unset the Zep integration disables itself (the client is
// nil and every Zep call is a no-op) so the agent still runs — useful for
// confirming the wiring without a Zep account. With GOOGLE_API_KEY unset the
// program prints the configured wiring and exits before calling the model.
package main

import (
	"context"
	"fmt"
	"log"
	"os"

	zepadk "github.com/getzep/zep/integrations/adk/go"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/agent/llmagent"
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
	if zep == nil {
		log.Println("ZEP_API_KEY not set: running without Zep memory (all Zep calls are no-ops).")
	} else {
		// Provision the Zep user and thread out of band before the first turn.
		// Both calls are idempotent. Passing a real name + email helps Zep
		// resolve the user's identity in the graph.
		if err := zepadk.EnsureUser(ctx, zep, userID, "Jane", "Smith", "jane@example.com"); err != nil {
			log.Printf("warning: could not ensure Zep user: %v", err)
		}
		if err := zepadk.EnsureThread(ctx, zep, sessionID, userID); err != nil {
			log.Printf("warning: could not ensure Zep thread: %v", err)
		}
	}

	// Graph-search tool the model can call to recall facts on demand.
	searchTool, err := zepadk.NewGraphSearchTool(zep)
	if err != nil {
		log.Fatalf("building graph search tool: %v", err)
	}

	// BeforeModelCallback persists each user turn and injects the Context Block.
	beforeModel := zepadk.NewBeforeModelCallback(zep, zepadk.WithUserMessageName("Jane"))

	// Without a Google API key we cannot construct the model; print the wiring
	// we would use and exit cleanly.
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

	run, err := runner.New(runner.Config{
		AppName:        appName,
		Agent:          zepAgent,
		SessionService: sessions,
		// Zep-backed memory.Service: ADK's built-in memory tooling reaches it
		// via ToolContext.SearchMemory.
		MemoryService: zepadk.NewMemoryService(zep),
	})
	if err != nil {
		log.Fatalf("creating runner: %v", err)
	}

	// Send a couple of turns. On the first turns Zep is still building the
	// graph (ingestion is asynchronous), so memory recall improves over time
	// and across sessions.
	prompts := []string{
		"Hi! My name is Jane and I'm a vegetarian who loves hiking.",
		"Can you suggest a meal for after my next hike?",
	}
	for _, prompt := range prompts {
		fmt.Printf("\n>>> %s\n", prompt)
		send(ctx, run, prompt)
	}
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
			fmt.Printf("<<< %s\n", zepadk.LastUserText(event.Content))
		}
	}
}
