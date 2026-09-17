package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	zep "github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"
	"github.com/getzep/zep-go/v4/option"
)

type TravelingTo struct {
	zep.EdgeBase `description:"A traveling to edge is an edge that connects two nodes"`
	TravelDate   string `description:"The date of the travel" json:"travel_date,omitempty"`
	Purpose      string `description:"The purpose of the travel" json:"purpose,omitempty"`
}

type BookedFlight struct {
	zep.EdgeBase `description:"A booked flight edge is an edge that connects two nodes"`
	FlightNumber string  `description:"The flight number of the flight" json:"flight_number,omitempty"`
	Departure    string  `description:"The departure time of the flight" json:"departure,omitempty"`
	Arrival      string  `description:"The arrival time of the flight" json:"arrival,omitempty"`
	Price        float64 `description:"The price of the flight" json:"price,omitempty"`
}

type Destination struct {
	zep.EntityBase  `description:"Travel destination"`
	DestinationName string  `description:"The name of the destination" json:"destination_name,omitempty"`
	Country         string  `description:"The country of the destination" json:"country,omitempty"`
	Latitude        float64 `description:"The latitude of the destination" json:"latitude,omitempty"`
	Longitude       float64 `description:"The longitude of the destination" json:"longitude,omitempty"`
	AirportCode     string  `description:"The airport code of the destination" json:"airport_code,omitempty"`
	AirportIATACode string  `description:"The airport IATA code of the destination" json:"airport_iata_code,omitempty"`
}

// unmarshalAttributes copies the attributes of a node or an edge into a
// declared ontology struct.
func unmarshalAttributes(attributes map[string]any, target any) error {
	data, err := json.Marshal(attributes)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}

func runEntityTypes() error {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		return fmt.Errorf("ZEP_API_KEY environment variable is not set")
	}

	client := zepclient.NewClient(
		option.WithAPIKey(apiKey),
	)

	ctx := context.Background()

	ontology, err := zep.BuildOntology(
		zep.Entities{
			"Destination": Destination{},
		},
		zep.Edges{
			"BOOKED_FLIGHT": {
				Model: BookedFlight{},
			},
			"TRAVELING_TO": {
				Model: TravelingTo{},
				SourceTargets: []*zep.EdgeSourceTarget{
					{
						Source: zep.String("User"),
						Target: zep.String("Destination"),
					},
				},
			},
		},
	)
	if err != nil {
		return fmt.Errorf("building ontology: %w", err)
	}

	// WARNING: Project.SetOntology replaces the project-level ontology.
	_, err = client.Project.SetOntology(ctx, ontology)
	if err != nil {
		return fmt.Errorf("setting the ontology: %w", err)
	}
	fmt.Println("Entity and edge types set for this project")

	// The search half of the example needs a graph that already holds travel
	// data. Set ZEP_ENTITY_TYPES_GRAPH_UUID to run it. v4 addresses a graph by
	// its server-generated UUID, and a user graph UUID is also accepted.
	graphUUID := os.Getenv("ZEP_ENTITY_TYPES_GRAPH_UUID")
	if graphUUID == "" {
		fmt.Println("Set ZEP_ENTITY_TYPES_GRAPH_UUID to also search the graph for these types")
		return nil
	}

	nodeResults, err := client.Graph.SearchNodes(ctx, graphUUID, &zep.GraphSearchNodesRequest{
		Body: &zep.SearchRequest{
			Query: "destination",
			Filters: &zep.SearchFilters{
				NodeLabels: []string{"Destination"},
			},
		},
	})
	if err != nil {
		return fmt.Errorf("searching graph: %w", err)
	}

	var destinations []Destination
	for _, node := range nodeResults.Results {
		var destination Destination
		if err := unmarshalAttributes(node.GetAttributes(), &destination); err != nil {
			fmt.Printf("Error converting node to struct: %v\n", err)
			continue
		}

		destinations = append(destinations, destination)
	}

	for _, destination := range destinations {
		fmt.Printf("Destination Country: %s\n", destination.Country)
		fmt.Printf("Destination Name: %s\n", destination.DestinationName)
	}

	edgeResults, err := client.Graph.SearchEdges(ctx, graphUUID, &zep.GraphSearchEdgesRequest{
		Body: &zep.SearchRequest{
			Query: "traveling to a destination",
		},
	})
	if err != nil {
		return fmt.Errorf("searching graph edges: %w", err)
	}

	var travelingToRelations []TravelingTo
	for _, edge := range edgeResults.Results {
		var travelingToRelation TravelingTo
		if err := unmarshalAttributes(edge.GetAttributes(), &travelingToRelation); err != nil {
			fmt.Printf("Error converting edge to struct: %v\n", err)
			continue
		}

		travelingToRelations = append(travelingToRelations, travelingToRelation)
	}

	for _, travelingToRelation := range travelingToRelations {
		fmt.Printf("Traveling to destination: %s\n", travelingToRelation.TravelDate)
		fmt.Printf("Traveling to purpose: %s\n", travelingToRelation.Purpose)
	}
	return nil
}
