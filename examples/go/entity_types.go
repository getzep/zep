package main

import (
	"context"
	"fmt"
	"os"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
)

func entityTypes() {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		fmt.Println("ZEP_API_KEY environment variable is not set")
		return
	}

	client := zepclient.NewClient(
		option.WithAPIKey(apiKey),
	)

	ctx := context.Background()

	type TravelingTo struct {
		zep.BaseEdge `name:"TRAVELING_TO" description:"A traveling to edge is an edge that connects two nodes"`
		TravelDate   string `description:"The destination of the travel" json:"travel_date,omitempty"`
		Purpose      string `description:"The distance of the travel" json:"purpose,omitempty"`
	}

	type BookedFlight struct {
		zep.BaseEdge `name:"BOOKED_FLIGHT" description:"A booked flight edge is an edge that connects two nodes"`
		FlightNumber string  `description:"The flight number of the flight" json:"flight_number,omitempty"`
		Departure    string  `description:"The departure time of the flight" json:"departure,omitempty"`
		Arrival      string  `description:"The arrival time of the flight" json:"arrival,omitempty"`
		Price        float64 `description:"The price of the flight" json:"price,omitempty"`
	}

	type Destination struct {
		zep.BaseEntity  `name:"Destination" description:"Travel destination"`
		DestinationName string  `description:"The name of the destination" json:"destination_name,omitempty"`
		Country         string  `description:"The country of the destination" json:"country,omitempty"`
		Latitude        float64 `description:"The latitude of the destination" json:"latitude,omitempty"`
		Longitude       float64 `description:"The longitude of the destination" json:"longitude,omitempty"`
		AirportCode     string  `description:"The airport code of the destination" json:"airport_code,omitempty"`
		AirportIATACode string  `description:"The airport IATA code of the destination" json:"airport_iata_code,omitempty"`
	}

	_, err := client.Graph.SetEntityTypes(
		ctx,
		[]zep.EntityDefinition{
			Destination{},
		},
		[]zep.EdgeDefinitionWithSourceTargets{
			{
				EdgeModel: BookedFlight{},
			},
			{
				EdgeModel: TravelingTo{},
				SourceTargets: []zep.EntityEdgeSourceTarget{
					{
						Source: zep.String("User"),
						Target: zep.String("Destination"),
					},
				},
			},
		},
	)
	if err != nil {
		fmt.Printf("Error setting entity types with base entity: %v\n", err)
		return
	}

	searchFilters := zep.SearchFilters{NodeLabels: []string{"Destination"}}
	searchResults, err := client.Graph.Search(
		ctx,
		&zep.GraphSearchQuery{
			UserID:        zep.String("<user_id>"),
			Query:         "destination",
			Scope:         zep.GraphSearchScopeNodes.Ptr(),
			SearchFilters: &searchFilters,
		},
	)
	if err != nil {
		fmt.Printf("Error searching graph: %v\n", err)
		return
	}

	var destinations []Destination
	for _, node := range searchResults.Nodes {
		var destination Destination
		err := zep.UnmarshalNodeAttributes(node.Attributes, &destination)
		if err != nil {
			fmt.Printf("Error converting node to struct: %v\n", err)
			continue
		}

		destinations = append(destinations, destination)
	}

	for _, destination := range destinations {
		fmt.Printf("Destination Country: %s\n", destination.Country)
		fmt.Printf("Destination Name: %s\n", destination.DestinationName)
	}

	var travelingToRelations []TravelingTo
	for _, edge := range searchResults.Edges {
		var travelingToRelation TravelingTo
		err := zep.UnmarshalEdgeAttributes(edge.Attributes, &travelingToRelation)
		if err != nil {
			fmt.Printf("Error converting edge to struct: %v\n", err)
			continue
		}

		travelingToRelations = append(travelingToRelations, travelingToRelation)
	}

	for _, travelingToRelation := range travelingToRelations {
		fmt.Printf("Traveling to destination: %s\n", travelingToRelation.TravelDate)
		fmt.Printf("Traveling to distance: %s\n", travelingToRelation.Purpose)
	}
}
