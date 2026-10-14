package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"
	zepgraph "github.com/getzep/zep-go/v4/graph"
	"github.com/getzep/zep-go/v4/option"
)

func waitForTask(ctx context.Context, client *zepclient.Client, taskUUID string) error {
	deadline := time.Now().Add(180 * time.Second)
	for time.Now().Before(deadline) {
		task, err := client.Task.Get(ctx, taskUUID)
		if err != nil {
			return fmt.Errorf("getting task %s: %w", taskUUID, err)
		}
		if task.Status != nil {
			switch strings.ToLower(string(*task.Status)) {
			case "succeeded", "completed":
				return nil
			case "failed", "error", "canceled", "cancelled", "partial":
				return fmt.Errorf("task %s ended with status %s", taskUUID, *task.Status)
			}
		}
		time.Sleep(2 * time.Second)
	}
	return fmt.Errorf("timed out waiting for task %s", taskUUID)
}

func runUserGraph() error {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		return fmt.Errorf("ZEP_API_KEY environment variable is not set")
	}

	client := zepclient.NewClient(option.WithAPIKey(apiKey))
	ctx := context.Background()

	user, err := client.User.Create(ctx, &zep.CreateUserRequest{
		UserID:    zep.String("examples-v4-e2e-go-" + uuid.NewString()),
		FirstName: zep.String("Paul"),
	})
	if err != nil {
		return fmt.Errorf("creating user: %w", err)
	}
	if user.UUID == nil {
		return fmt.Errorf("creating user returned no UUID")
	}
	userUUID := *user.UUID
	fmt.Printf("User UUID: %s\n", userUUID)

	thread, err := client.Thread.Create(ctx, &zep.CreateThreadRequest{UserUUID: userUUID})
	if err != nil {
		return fmt.Errorf("creating thread: %w", err)
	}
	if thread.UUID == nil {
		return fmt.Errorf("creating thread returned no UUID")
	}
	threadUUID := *thread.UUID
	fmt.Printf("Thread UUID: %s\n", threadUUID)

	var messages []*zep.AddMessage
	for _, conversation := range history {
		messages = append(messages, conversation...)
	}
	messageResult, err := client.Thread.AddMessages(ctx, threadUUID, &zep.AddMessagesRequest{
		Messages:      messages,
		ReturnContext: zep.Bool(true),
	})
	if err != nil {
		return fmt.Errorf("adding thread messages: %w", err)
	}
	if messageResult.Task != nil && messageResult.Task.UUID != nil {
		if err := waitForTask(ctx, client, *messageResult.Task.UUID); err != nil {
			return err
		}
	}
	if messageResult.Context != nil {
		fmt.Printf("Context returned with messages:\n%s\n", *messageResult.Context)
	}

	threadContext, err := client.Thread.GetContext(ctx, threadUUID, &zep.ThreadGetContextRequest{})
	if err != nil {
		return fmt.Errorf("getting thread context: %w", err)
	}
	fmt.Printf("Thread context:\n%+v\n", threadContext.Context)

	episodePage, err := client.Graph.Episode.List(ctx, userUUID, &zepgraph.EpisodeListRequest{
		PageSize: zep.Int(3),
	})
	if err != nil {
		return fmt.Errorf("listing episodes: %w", err)
	}
	var listedEpisodes []*zep.Episode
	episodeIterator := episodePage.Iterator()
	for episodeIterator.Next(ctx) {
		listedEpisodes = append(listedEpisodes, episodeIterator.Current())
	}
	if err := episodeIterator.Err(); err != nil {
		return fmt.Errorf("iterating episodes: %w", err)
	}
	fmt.Printf("Episodes for user graph:\n%+v\n", listedEpisodes)
	if len(listedEpisodes) > 0 && listedEpisodes[0].UUID != nil {
		episode, err := client.Graph.Episode.Get(ctx, userUUID, *listedEpisodes[0].UUID)
		if err != nil {
			return fmt.Errorf("getting episode: %w", err)
		}
		fmt.Printf("First episode:\n%+v\n", episode)
	}

	edgePage, err := client.Graph.Edge.List(ctx, userUUID, &zepgraph.EdgeListRequest{
		PageSize: zep.Int(10),
	})
	if err != nil {
		return fmt.Errorf("listing edges: %w", err)
	}
	var edges []*zep.Edge
	edgeIterator := edgePage.Iterator()
	for edgeIterator.Next(ctx) {
		edges = append(edges, edgeIterator.Current())
	}
	if err := edgeIterator.Err(); err != nil {
		return fmt.Errorf("iterating edges: %w", err)
	}
	fmt.Printf("Edges for user graph:\n%+v\n", edges)
	if len(edges) > 0 && edges[0].UUID != nil {
		edge, err := client.Graph.Edge.Get(ctx, userUUID, *edges[0].UUID)
		if err != nil {
			return fmt.Errorf("getting edge: %w", err)
		}
		fmt.Printf("First edge:\n%+v\n", edge)
	}

	nodePage, err := client.Graph.Node.List(ctx, userUUID, &zepgraph.NodeListRequest{
		PageSize: zep.Int(10),
	})
	if err != nil {
		return fmt.Errorf("listing nodes: %w", err)
	}
	var nodes []*zep.Node
	nodeIterator := nodePage.Iterator()
	for nodeIterator.Next(ctx) {
		nodes = append(nodes, nodeIterator.Current())
	}
	if err := nodeIterator.Err(); err != nil {
		return fmt.Errorf("iterating nodes: %w", err)
	}
	fmt.Printf("Nodes for user graph:\n%+v\n", nodes)
	if len(nodes) > 0 && nodes[0].UUID != nil {
		node, err := client.Graph.Node.Get(ctx, userUUID, *nodes[0].UUID)
		if err != nil {
			return fmt.Errorf("getting node: %w", err)
		}
		fmt.Printf("First node:\n%+v\n", node)
	}

	fmt.Println("Searching user graph memory...")
	graphContext, err := client.Graph.GetContext(ctx, userUUID, &zep.GraphContextRequest{
		Query: "What is the weather in San Francisco?",
	})
	if err != nil {
		return fmt.Errorf("getting graph context: %w", err)
	}
	fmt.Printf("%+v\n", graphContext.Context)

	fmt.Println("Adding text, JSON, and message episodes...")
	episodes := []struct {
		data    string
		episode *zepgraph.AddEpisodeRequestType
	}{
		{data: "The user is an avid fan of Eric Clapton", episode: zepgraph.AddEpisodeRequestTypeText.Ptr()},
		{data: `{"name":"Eric Clapton","age":78,"genre":"Rock"}`, episode: zepgraph.AddEpisodeRequestTypeJSON.Ptr()},
		{data: "Paul (user): I went to an Eric Clapton concert last night", episode: zepgraph.AddEpisodeRequestTypeMessage.Ptr()},
	}
	var lastTaskUUID string
	for _, item := range episodes {
		result, err := client.Graph.Episode.Add(ctx, userUUID, &zepgraph.AddEpisodeRequest{
			Data: item.data,
			Type: item.episode,
		})
		if err != nil {
			return fmt.Errorf("adding episode: %w", err)
		}
		if result.Task != nil && result.Task.UUID != nil {
			lastTaskUUID = *result.Task.UUID
		}
	}
	if lastTaskUUID != "" {
		fmt.Println("Waiting for the episode batch to be processed...")
		if err := waitForTask(ctx, client, lastTaskUUID); err != nil {
			return err
		}
	}

	updatedNodes, err := client.Graph.Node.List(ctx, userUUID, &zepgraph.NodeListRequest{
		PageSize: zep.Int(100),
	})
	if err != nil {
		return fmt.Errorf("listing updated nodes: %w", err)
	}
	var claptonNode *zep.Node
	updatedNodeIterator := updatedNodes.Iterator()
	var updatedNodeList []*zep.Node
	for updatedNodeIterator.Next(ctx) {
		updatedNodeList = append(updatedNodeList, updatedNodeIterator.Current())
	}
	if err := updatedNodeIterator.Err(); err != nil {
		return fmt.Errorf("iterating updated nodes: %w", err)
	}
	for _, node := range updatedNodeList {
		if node.Name != nil && *node.Name == "Eric Clapton" {
			claptonNode = node
			break
		}
	}
	if claptonNode == nil || claptonNode.UUID == nil {
		fmt.Println("Eric Clapton node was not found")
		return nil
	}

	edgeSearch, err := client.Graph.SearchEdges(ctx, userUUID, &zep.SearchEdgesRequest{
		Query:          "Eric Clapton",
		CenterNodeUUID: claptonNode.UUID,
		PageSize:       zep.Int(10),
	})
	if err != nil {
		return fmt.Errorf("searching edges: %w", err)
	}
	var searchedEdges []*zep.Edge
	edgeSearchIterator := edgeSearch.Iterator()
	for edgeSearchIterator.Next(ctx) {
		searchedEdges = append(searchedEdges, edgeSearchIterator.Current())
	}
	if err := edgeSearchIterator.Err(); err != nil {
		return fmt.Errorf("iterating searched edges: %w", err)
	}
	fmt.Printf("Eric Clapton edge search:\n%+v\n", searchedEdges)

	nodeSearch, err := client.Graph.SearchNodes(ctx, userUUID, &zep.SearchNodesRequest{
		Query:          "Eric Clapton",
		CenterNodeUUID: claptonNode.UUID,
		PageSize:       zep.Int(10),
	})
	if err != nil {
		return fmt.Errorf("searching nodes: %w", err)
	}
	var searchedNodes []*zep.Node
	nodeSearchIterator := nodeSearch.Iterator()
	for nodeSearchIterator.Next(ctx) {
		searchedNodes = append(searchedNodes, nodeSearchIterator.Current())
	}
	if err := nodeSearchIterator.Err(); err != nil {
		return fmt.Errorf("iterating searched nodes: %w", err)
	}
	fmt.Printf("Eric Clapton node search:\n%+v\n", searchedNodes)
	return nil
}
