package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	"github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"
	"github.com/getzep/zep-go/v4/option"
)

func runEntityTypes() error {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		return fmt.Errorf("ZEP_API_KEY environment variable is not set")
	}

	client := zepclient.NewClient(option.WithAPIKey(apiKey))
	ctx := context.Background()
	searchUserUUID := os.Getenv("ZEP_ENTITY_TYPES_USER_UUID")
	if searchUserUUID == "" {
		return fmt.Errorf("ZEP_ENTITY_TYPES_USER_UUID must be a Zep-returned user UUID")
	}

	type TravelingTo struct {
		zep.EdgeBase `description:"A relationship that connects a traveler to a destination"`
		TravelDate   string `description:"The date of travel" json:"travel_date,omitempty"`
		Purpose      string `description:"The purpose of the travel" json:"purpose,omitempty"`
	}

	type BookedFlight struct {
		zep.EdgeBase `description:"A relationship that records a booked flight"`
		FlightNumber string  `description:"The flight number" json:"flight_number,omitempty"`
		Departure    string  `description:"The departure time" json:"departure,omitempty"`
		Arrival      string  `description:"The arrival time" json:"arrival,omitempty"`
		Price        float64 `description:"The price of the flight" json:"price,omitempty"`
	}

	type Destination struct {
		zep.EntityBase  `description:"A travel destination"`
		DestinationName string  `description:"The name of the destination" json:"destination_name,omitempty"`
		Country         string  `description:"The country of the destination" json:"country,omitempty"`
		Latitude        float64 `description:"The latitude of the destination" json:"latitude,omitempty"`
		Longitude       float64 `description:"The longitude of the destination" json:"longitude,omitempty"`
		AirportCode     string  `description:"The airport code of the destination" json:"airport_code,omitempty"`
		AirportIATACode string  `description:"The airport IATA code of the destination" json:"airport_iata_code,omitempty"`
	}

	ontology, err := zep.BuildOntology(
		zep.Entities{"Destination": Destination{}},
		zep.Edges{
			"BOOKED_FLIGHT": {Model: BookedFlight{}},
			"TRAVELING_TO": {
				Model: TravelingTo{},
				SourceTargets: []*zep.EdgeSourceTarget{
					{Source: zep.String("User"), Target: zep.String("Destination")},
				},
			},
		},
	)
	if err != nil {
		return fmt.Errorf("building ontology: %w", err)
	}
	if _, err := client.Graph.SetOntology(ctx, searchUserUUID, ontology); err != nil {
		return fmt.Errorf("setting user graph ontology: %w", err)
	}
	fmt.Println("Entity and edge types set for this user graph")

	searchResults, err := client.Graph.SearchNodes(ctx, searchUserUUID, &zep.SearchNodesRequest{
		Query:    "destination",
		PageSize: zep.Int(10),
		Filters:  &zep.NodeSearchFilters{NodeLabels: []string{"Destination"}},
	})
	if err != nil {
		return fmt.Errorf("searching graph nodes: %w", err)
	}

	nodeIterator := searchResults.Iterator()
	for nodeIterator.Next(ctx) {
		node := nodeIterator.Current()
		var destination Destination
		attributes, err := json.Marshal(node.Attributes)
		if err != nil {
			fmt.Printf("Error converting node attributes: %v\n", err)
			continue
		}
		if err := json.Unmarshal(attributes, &destination); err != nil {
			fmt.Printf("Error converting node to struct: %v\n", err)
			continue
		}
		fmt.Printf("Destination Country: %s\n", destination.Country)
		fmt.Printf("Destination Name: %s\n", destination.DestinationName)
	}
	if err := nodeIterator.Err(); err != nil {
		return fmt.Errorf("iterating destination nodes: %w", err)
	}

	edgeSearchResults, err := client.Graph.SearchEdges(ctx, searchUserUUID, &zep.SearchEdgesRequest{
		Query:    "traveling to a destination",
		PageSize: zep.Int(10),
	})
	if err != nil {
		return fmt.Errorf("searching graph edges: %w", err)
	}

	edgeIterator := edgeSearchResults.Iterator()
	for edgeIterator.Next(ctx) {
		edge := edgeIterator.Current()
		var travelingToRelation TravelingTo
		attributes, err := json.Marshal(edge.Attributes)
		if err != nil {
			fmt.Printf("Error converting edge attributes: %v\n", err)
			continue
		}
		if err := json.Unmarshal(attributes, &travelingToRelation); err != nil {
			fmt.Printf("Error converting edge to struct: %v\n", err)
			continue
		}
		fmt.Printf("Traveling to destination: %s\n", travelingToRelation.TravelDate)
		fmt.Printf("Traveling to purpose: %s\n", travelingToRelation.Purpose)
	}
	if err := edgeIterator.Err(); err != nil {
		return fmt.Errorf("iterating travel edges: %w", err)
	}
	return nil
}
