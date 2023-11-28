package tasks

import (
	"context"
	"reflect"
	"testing"

	"github.com/stretchr/testify/assert"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
)

func TestCallNERService(t *testing.T) {
	texts := []string{
		`But Google is starting from behind. The company made a late push
    into hardware, and Apple's Siri, available on iPhones, and Amazon's Alexa
    software, which runs on its Echo and Dot devices, have clear leads in
    consumer adoption.`,
		`South Korea’s Kospi gained as much as 1%, on track for its sixth 
    daily advance. Samsung Electronics Co. and SK Hynix Inc. were among the biggest 
    contributors to the benchmark after China said their US rival Micron Technology 
    Inc. had failed to pass a cybersecurity review. "I think you’re gonna see that 
    begin to thaw very shortly,” between the US and China, Biden said on Sunday 
    after a Group-of-Seven summit in Japan. He added that his administration was 
    considering whether to lift sanctions on Chinese Defense Minister Li Shangfu.`,
	}
	// Create messages with the texts
	textData := createMessages(texts)

	// Call the NER service
	response, err := callNERTask(context.Background(), appState, textData)
	assert.NoError(t, err)

	// Check the response
	assert.Equal(t, len(response.Texts), len(texts))

	// Check the uuids
	for i := range textData {
		validateUUID(t, response.Texts[i].UUID, textData[i].TextUUID)
	}

	expectedEntities := [][]models.Entity{{
		{
			Name:  "Google",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 4,
					End:   10,
					Text:  "Google",
				},
			},
		},
		{
			Name:  "Apple",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 88,
					End:   93,
					Text:  "Apple",
				},
			},
		},
		{
			Name:  "Siri",
			Label: "PERSON",
			Matches: []models.EntityMatch{
				{
					Start: 96,
					End:   100,
					Text:  "Siri",
				},
			},
		},
		{
			Name:  "iPhones",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 115,
					End:   122,
					Text:  "iPhones",
				},
			},
		},
		{
			Name:  "Amazon",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 128,
					End:   134,
					Text:  "Amazon",
				},
			},
		},
		{
			Name:  "Alexa",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 137,
					End:   142,
					Text:  "Alexa",
				},
			},
		},
		{
			Name:  "Echo",
			Label: "LOC",
			Matches: []models.EntityMatch{
				{
					Start: 175,
					End:   179,
					Text:  "Echo",
				},
			},
		},
	}, {
		{
			Name:  "South Korea’s",
			Label: "GPE",
			Matches: []models.EntityMatch{
				{
					Start: 0,
					End:   13,
					Text:  "South Korea’s",
				},
			},
		},
		{
			Name:  "As much as 1%",
			Label: "PERCENT",
			Matches: []models.EntityMatch{
				{
					Start: 27,
					End:   40,
					Text:  "as much as 1%",
				},
			},
		},
		{
			Name:  "Sixth",
			Label: "ORDINAL",
			Matches: []models.EntityMatch{
				{
					Start: 59,
					End:   64,
					Text:  "sixth",
				},
			},
		},
		{
			Name:  "Daily",
			Label: "DATE",
			Matches: []models.EntityMatch{
				{
					Start: 70,
					End:   75,
					Text:  "daily",
				},
			},
		},
		{
			Name:  "Samsung Electronics Co.",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 85,
					End:   108,
					Text:  "Samsung Electronics Co.",
				},
			},
		},
		{
			Name:  "SK Hynix Inc.",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 113,
					End:   126,
					Text:  "SK Hynix Inc.",
				},
			},
		},
		{
			Name:  "China",
			Label: "GPE",
			Matches: []models.EntityMatch{
				{
					Start: 191,
					End:   196,
					Text:  "China",
				},
				{
					Start: 372,
					End:   377,
					Text:  "China",
				},
			},
		},
		{
			Name:  "US",
			Label: "GPE",
			Matches: []models.EntityMatch{
				{
					Start: 208,
					End:   210,
					Text:  "US",
				},
				{
					Start: 365,
					End:   367,
					Text:  "US",
				},
			},
		},
		{
			Name:  "Micron Technology \n    Inc.",
			Label: "ORG",
			Matches: []models.EntityMatch{
				{
					Start: 217,
					End:   244,
					Text:  "Micron Technology \n    Inc.",
				},
			},
		},
		{
			Name:  "Biden",
			Label: "PERSON",
			Matches: []models.EntityMatch{
				{
					Start: 379,
					End:   384,
					Text:  "Biden",
				},
			},
		},
		{
			Name:  "Sunday",
			Label: "DATE",
			Matches: []models.EntityMatch{
				{
					Start: 393,
					End:   399,
					Text:  "Sunday",
				},
			},
		},
		{
			Name:  "Seven",
			Label: "CARDINAL",
			Matches: []models.EntityMatch{
				{
					Start: 422,
					End:   427,
					Text:  "Seven",
				},
			},
		},
		{
			Name:  "Japan",
			Label: "GPE",
			Matches: []models.EntityMatch{
				{
					Start: 438,
					End:   443,
					Text:  "Japan",
				},
			},
		},
		{
			Name:  "Chinese",
			Label: "NORP",
			Matches: []models.EntityMatch{
				{
					Start: 528,
					End:   535,
					Text:  "Chinese",
				},
			},
		},
		{
			Name:  "Li Shangfu",
			Label: "PERSON",
			Matches: []models.EntityMatch{
				{
					Start: 553,
					End:   563,
					Text:  "Li Shangfu",
				},
			},
		},
	}}

	// Check if the entities match the expected values
	for i := range expectedEntities {
		validateEntities(t, response.Texts[i].Entities, expectedEntities[i])
	}
}

func createMessages(texts []string) []models.TextData {
	td := make([]models.TextData, len(texts))
	for i, text := range texts {
		td[i] = models.TextData{
			TextUUID: uuid.New(),
			Text:     text,
			Language: "en",
		}
	}
	return td
}

func validateUUID(t *testing.T, got string, want uuid.UUID) {
	gotUUID, err := uuid.Parse(got)
	assert.NoError(t, err)
	assert.Equal(t, gotUUID, want)
}

func validateEntities(t *testing.T, got []models.Entity, want []models.Entity) {
	for i := range want {
		assert.Equal(t, got[i], want[i])
		if !reflect.DeepEqual(got[i], want[i]) {
			t.Errorf("Entities do not match: got %+v want %+v", got[i], want[i])
		}
	}
}

func TestExtractEntities(t *testing.T) {
	testCases := []struct {
		name     string
		entities interface{}
		want     []map[string]interface{}
	}{
		{
			name: "With Data",
			entities: []models.Entity{{
				Name:  "Google",
				Label: "ORG",
				Matches: []models.EntityMatch{
					{
						Start: 4,
						End:   10,
						Text:  "Google",
					},
				},
			},
			},
			want: []map[string]interface{}{
				{
					"Label": "ORG",
					"Name":  "Google",
					"Matches": []interface{}{
						map[string]interface{}{
							"Start": 4,
							"End":   10,
							"Text":  "Google",
						},
					},
				},
			},
		},
		{
			name:     "No Data",
			entities: []models.Entity{},
			want:     []map[string]interface{}{},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			got := extractEntities(tc.entities)
			assert.Equal(t, tc.want, got)
		})
	}
}
