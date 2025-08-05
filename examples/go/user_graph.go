package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/graph"
	"github.com/getzep/zep-go/v3/option"
)

func main() {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		fmt.Println("ZEP_API_KEY environment variable is not set")
		return
	}

	client := zepclient.NewClient(
		option.WithAPIKey(apiKey),
	)

	ctx := context.Background()

	userID := uuid.New().String()
	threadID := uuid.New().String()

	// Create a user
	userRequest := &zep.CreateUserRequest{
		UserID:    userID,
		FirstName: zep.String("Paul"),
	}
	_, err := client.User.Add(ctx, userRequest)
	if err != nil {
		fmt.Printf("Error creating user: %v\n", err)
		return
	}
	fmt.Printf("User %s created\n", userID)

	// Create a thread
	_, err = client.Thread.Create(ctx, &zep.CreateThreadRequest{
		ThreadID: threadID,
		UserID:   userID,
	})
	if err != nil {
		fmt.Printf("Error creating thread: %v\n", err)
		return
	}
	fmt.Printf("thread %s created\n", threadID)

	// Add messages to the thread
	for _, message := range history[0] {
		_, err = client.Thread.AddMessages(ctx, threadID, &zep.AddThreadMessagesRequest{
			Messages: []*zep.Message{
				{Role: message.Role, Name: message.Name, Content: message.Content},
			},
			ReturnContext: zep.Bool(true),
		})
		if err != nil {
			fmt.Printf("Error adding message: %v\n", err)
			return
		}
	}

	fmt.Println("Waiting for the graph to be updated...")
	time.Sleep(10 * time.Second)

	fmt.Println("Getting memory for thread")

	threadMemory, err := client.Thread.GetUserContext(
		ctx,
		threadID,
		&zep.ThreadGetUserContextRequest{
			Mode: zep.ThreadGetUserContextRequestModeSummary.Ptr(),
		},
	)
	if err != nil {
		fmt.Printf("Error getting thread memory: %v\n", err)
		return
	}
	fmt.Printf("%+v\n", threadMemory.Context)

	fmt.Println("Getting episodes for user")
	episodeResult, err := client.Graph.Episode.GetByUserID(ctx, userID, &graph.EpisodeGetByUserIDRequest{
		Lastn: zep.Int(3),
	})
	if err != nil {
		fmt.Printf("Error getting episodes: %v\n", err)
		return
	}
	fmt.Printf("Episodes for user %s:\n", userID)
	fmt.Printf("%+v\n", episodeResult.Episodes)

	if len(episodeResult.Episodes) > 0 {
		episode, err := client.Graph.Episode.Get(ctx, episodeResult.Episodes[0].UUID)
		if err != nil {
			fmt.Printf("Error getting episode: %v\n", err)
			return
		}
		fmt.Printf("%+v\n", episode)
	}

	edges, err := client.Graph.Edge.GetByUserID(ctx, userID, &zep.GraphEdgesRequest{})
	if err != nil {
		fmt.Printf("Error getting edges: %v\n", err)
		return
	}
	fmt.Printf("Edges for user %s:\n", userID)
	fmt.Printf("%+v\n", edges)

	if len(edges) > 0 {
		edge, err := client.Graph.Edge.Get(ctx, edges[0].UUID)
		if err != nil {
			fmt.Printf("Error getting edge: %v\n", err)
			return
		}
		fmt.Printf("%+v\n", edge)
	}

	nodes, err := client.Graph.Node.GetByUserID(ctx, userID, &zep.GraphNodesRequest{})
	if err != nil {
		fmt.Printf("Error getting nodes: %v\n", err)
		return
	}
	fmt.Printf("Nodes for user %s:\n", userID)
	fmt.Printf("%+v\n", nodes)

	if len(nodes) > 0 {
		node, err := client.Graph.Node.Get(ctx, nodes[0].UUID)
		if err != nil {
			fmt.Printf("Error getting node: %v\n", err)
			return
		}
		fmt.Printf("%+v\n", node)
	}

	fmt.Println("Searching user graph memory...")
	graphSearchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
		UserID: zep.String(userID),
		Query:  "What is the weather in San Francisco?",
	})
	if err != nil {
		fmt.Printf("Error searching graph: %v\n", err)
		return
	}
	fmt.Printf("%+v\n", graphSearchResults.Edges)

	fmt.Println("Adding a new text episode to the graph...")
	_, err = client.Graph.Add(ctx, &zep.AddDataRequest{
		UserID: zep.String(userID),
		Type:   zep.GraphDataTypeText,
		Data:   "The user is an avid fan of Eric Clapton",
	})
	if err != nil {
		fmt.Printf("Error adding text episode: %v\n", err)
		return
	}
	fmt.Println("Text episode added")

	fmt.Println("Adding a new JSON episode to the graph...")
	jsonString := `{"name": "Eric Clapton", "age": 78, "genre": "Rock"}`
	_, err = client.Graph.Add(ctx, &zep.AddDataRequest{
		UserID: zep.String(userID),
		Type:   zep.GraphDataTypeJSON,
		Data:   jsonString,
	})
	if err != nil {
		fmt.Printf("Error adding JSON episode: %v\n", err)
		return
	}
	fmt.Println("JSON episode added")

	fmt.Println("Adding a new message episode to the graph...")
	message := "Paul (user): I went to Eric Clapton concert last night"
	_, err = client.Graph.Add(ctx, &zep.AddDataRequest{
		UserID: zep.String(userID),
		Type:   zep.GraphDataTypeMessage,
		Data:   message,
	})
	if err != nil {
		fmt.Printf("Error adding message episode: %v\n", err)
		return
	}
	fmt.Println("Message episode added")

	fmt.Println("Waiting for the graph to be updated...")
	time.Sleep(30 * time.Second)

	fmt.Println("Getting nodes from the graph...")
	updatedNodes, err := client.Graph.Node.GetByUserID(ctx, userID, &zep.GraphNodesRequest{})
	if err != nil {
		fmt.Printf("Error getting updated nodes: %v\n", err)
		return
	}
	fmt.Printf("%+v\n", updatedNodes)

	fmt.Println("Finding Eric Clapton in the graph...")
	var claptonNode *zep.EntityNode
	for _, node := range updatedNodes {
		if node.Name == "Eric Clapton" {
			claptonNode = node
			break
		}
	}
	fmt.Printf("%+v\n", claptonNode)

	if claptonNode != nil {
		fmt.Println("Performing Eric Clapton centered edge search...")
		edgeSearchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
			UserID:         zep.String(userID),
			Query:          "Eric Clapton",
			CenterNodeUUID: &claptonNode.UUID,
			Scope:          zep.GraphSearchScopeEdges.Ptr(),
		})
		if err != nil {
			fmt.Printf("Error performing edge search: %v\n", err)
			return
		}
		fmt.Printf("%+v\n", edgeSearchResults.Edges)

		fmt.Println("Performing Eric Clapton centered node search...")
		nodeSearchResults, err := client.Graph.Search(ctx, &zep.GraphSearchQuery{
			UserID:         zep.String(userID),
			Query:          "Eric Clapton",
			CenterNodeUUID: &claptonNode.UUID,
			Scope:          zep.GraphSearchScopeNodes.Ptr(),
		})
		if err != nil {
			fmt.Printf("Error performing node search: %v\n", err)
			return
		}
		fmt.Printf("%+v\n", nodeSearchResults.Nodes)
	}
}
