package main

import (
	"context"
	"fmt"
	"os"
	"time"

	zep "github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"
	"github.com/getzep/zep-go/v4/graph"
	"github.com/getzep/zep-go/v4/option"
)

func runUserGraph() error {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		return fmt.Errorf("ZEP_API_KEY environment variable is not set")
	}

	client := zepclient.NewClient(
		option.WithAPIKey(apiKey),
	)

	ctx := context.Background()

	// v4 addresses every user, thread and graph by a server-generated UUID.
	// The create calls below do not send a developer identifier. The example
	// keeps the UUIDs that the server returns and uses them as addresses.
	user, err := client.User.Create(ctx, &zep.CreateUserRequest{
		FirstName: zep.String("Paul"),
	})
	if err != nil {
		return fmt.Errorf("creating user: %w", err)
	}
	userUUID := user.GetUUID()
	graphUUID := user.GetGraphUUID()
	if userUUID == nil || graphUUID == nil {
		return fmt.Errorf("the server did not return a user UUID and a graph UUID")
	}
	fmt.Printf("User %s created with graph %s\n", *userUUID, *graphUUID)

	thread, err := client.Thread.Create(ctx, &zep.CreateThreadRequest{
		UserUUID: *userUUID,
	})
	if err != nil {
		return fmt.Errorf("creating thread: %w", err)
	}
	threadUUID := thread.GetUUID()
	if threadUUID == nil {
		return fmt.Errorf("the server did not return a thread UUID")
	}
	fmt.Printf("Thread %s created\n", *threadUUID)

	// Add messages to the thread
	for _, conversation := range history {
		for _, message := range conversation {
			_, err = client.Thread.AddMessages(ctx, *threadUUID, &zep.AddMessagesRequest{
				Messages: []*zep.AddMessage{
					{Role: message.Role, Name: message.Name, Content: message.Content},
				},
				ReturnContext: zep.Bool(true),
			})
			if err != nil {
				return fmt.Errorf("adding message: %w", err)
			}
		}
	}

	fmt.Println("Waiting for the graph to be updated...")
	time.Sleep(10 * time.Second)

	fmt.Println("Getting the context block of the thread")
	threadContext, err := client.Thread.GetContext(ctx, *threadUUID, &zep.ThreadGetContextRequest{})
	if err != nil {
		return fmt.Errorf("getting thread context: %w", err)
	}
	fmt.Printf("%+v\n", threadContext.GetContext())

	fmt.Println("Getting the episodes of the graph")
	episodePage, err := client.Graph.Episode.List(ctx, *graphUUID, &graph.EpisodeListRequest{
		Limit: zep.Int(3),
		Body:  &zep.ArtifactListRequest{},
	})
	if err != nil {
		return fmt.Errorf("getting episodes: %w", err)
	}
	fmt.Printf("Episodes of graph %s:\n", *graphUUID)
	fmt.Printf("%+v\n", episodePage.Results)

	if len(episodePage.Results) > 0 {
		episode, err := client.Graph.Episode.Get(ctx, *graphUUID, *episodePage.Results[0].GetUUID())
		if err != nil {
			return fmt.Errorf("getting episode: %w", err)
		}
		fmt.Printf("%+v\n", episode)
	}

	edgePage, err := client.Graph.Edge.List(ctx, *graphUUID, &graph.EdgeListRequest{
		Body: &zep.ArtifactListRequest{},
	})
	if err != nil {
		return fmt.Errorf("getting edges: %w", err)
	}
	fmt.Printf("Edges of graph %s:\n", *graphUUID)
	fmt.Printf("%+v\n", edgePage.Results)

	if len(edgePage.Results) > 0 {
		edge, err := client.Graph.Edge.Get(ctx, *graphUUID, *edgePage.Results[0].GetUUID())
		if err != nil {
			return fmt.Errorf("getting edge: %w", err)
		}
		fmt.Printf("%+v\n", edge)
	}

	nodePage, err := client.Graph.Node.List(ctx, *graphUUID, &graph.NodeListRequest{
		Body: &zep.ArtifactListRequest{},
	})
	if err != nil {
		return fmt.Errorf("getting nodes: %w", err)
	}
	fmt.Printf("Nodes of graph %s:\n", *graphUUID)
	fmt.Printf("%+v\n", nodePage.Results)

	if len(nodePage.Results) > 0 {
		node, err := client.Graph.Node.Get(ctx, *graphUUID, *nodePage.Results[0].GetUUID())
		if err != nil {
			return fmt.Errorf("getting node: %w", err)
		}
		fmt.Printf("%+v\n", node)
	}

	fmt.Println("Searching the graph of the user...")
	searchResults, err := client.Graph.SearchEdges(ctx, *graphUUID, &zep.GraphSearchEdgesRequest{
		Body: &zep.SearchRequest{
			Query: "What is the weather in San Francisco?",
		},
	})
	if err != nil {
		return fmt.Errorf("searching graph: %w", err)
	}
	fmt.Printf("%+v\n", searchResults.Results)

	fmt.Println("Adding a new text episode to the graph...")
	_, err = client.Graph.Episode.Add(ctx, *graphUUID, &graph.AddEpisodeRequest{
		Type: graph.V4AddEpisodeRequestTypeText.Ptr(),
		Data: "The user is an avid fan of Eric Clapton",
	})
	if err != nil {
		return fmt.Errorf("adding text episode: %w", err)
	}
	fmt.Println("Text episode added")

	fmt.Println("Adding a new JSON episode to the graph...")
	jsonString := `{"name": "Eric Clapton", "age": 78, "genre": "Rock"}`
	_, err = client.Graph.Episode.Add(ctx, *graphUUID, &graph.AddEpisodeRequest{
		Type: graph.V4AddEpisodeRequestTypeJSON.Ptr(),
		Data: jsonString,
	})
	if err != nil {
		return fmt.Errorf("adding JSON episode: %w", err)
	}
	fmt.Println("JSON episode added")

	fmt.Println("Adding a new message episode to the graph...")
	message := "Paul (user): I went to Eric Clapton concert last night"
	_, err = client.Graph.Episode.Add(ctx, *graphUUID, &graph.AddEpisodeRequest{
		Type: graph.V4AddEpisodeRequestTypeMessage.Ptr(),
		Data: message,
	})
	if err != nil {
		return fmt.Errorf("adding message episode: %w", err)
	}
	fmt.Println("Message episode added")

	fmt.Println("Waiting for the graph to be updated...")
	time.Sleep(30 * time.Second)

	fmt.Println("Getting nodes from the graph...")
	updatedNodePage, err := client.Graph.Node.List(ctx, *graphUUID, &graph.NodeListRequest{
		Body: &zep.ArtifactListRequest{},
	})
	if err != nil {
		return fmt.Errorf("getting updated nodes: %w", err)
	}
	fmt.Printf("%+v\n", updatedNodePage.Results)

	fmt.Println("Finding Eric Clapton in the graph...")
	var claptonNode *zep.Node
	for _, node := range updatedNodePage.Results {
		if node.GetName() != nil && *node.GetName() == "Eric Clapton" {
			claptonNode = node
			break
		}
	}
	fmt.Printf("%+v\n", claptonNode)

	if claptonNode != nil {
		fmt.Println("Performing Eric Clapton centered edge search...")
		edgeSearchResults, err := client.Graph.SearchEdges(ctx, *graphUUID, &zep.GraphSearchEdgesRequest{
			Body: &zep.SearchRequest{
				Query:          "Eric Clapton",
				CenterNodeUUID: claptonNode.GetUUID(),
				Reranker:       zep.V4SearchRequestRerankerNodeDistance.Ptr(),
			},
		})
		if err != nil {
			return fmt.Errorf("performing edge search: %w", err)
		}
		fmt.Printf("%+v\n", edgeSearchResults.Results)

		fmt.Println("Performing Eric Clapton centered node search...")
		nodeSearchResults, err := client.Graph.SearchNodes(ctx, *graphUUID, &zep.GraphSearchNodesRequest{
			Body: &zep.SearchRequest{
				Query:          "Eric Clapton",
				CenterNodeUUID: claptonNode.GetUUID(),
				Reranker:       zep.V4SearchRequestRerankerNodeDistance.Ptr(),
			},
		})
		if err != nil {
			return fmt.Errorf("performing node search: %w", err)
		}
		fmt.Printf("%+v\n", nodeSearchResults.Results)
	}
	return nil
}
