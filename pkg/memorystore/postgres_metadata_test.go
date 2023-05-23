package memorystore

import (
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/test"
	"github.com/stretchr/testify/assert"
)

func TestPutMetadata(t *testing.T) {
	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	testMessages := []models.Message{
		{
			Role:    "user",
			Content: "Hello",
		},
		{
			Role:    "human",
			Content: "Hello again",
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_overwrite": "value_to_overwrite",
				},
			},
		},
		{
			Role:    "human",
			Content: "Hello again",
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_delete": "value_to_delete",
				},
			},
		},
		{
			Role:     "bot",
			Content:  "Hi there!",
			Metadata: map[string]interface{}{"existing_metadata": "this is existing metadata"},
		},
		{
			Role:    "human",
			Content: "Bonjour!",
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"some_other_system_key": "some_other_system_value",
				},
			},
		},
		{
			Role:     "human",
			Content:  "Hi!",
			Metadata: map[string]interface{}{"existing_key": "existing_value"},
		},
	}

	// Call putMessages function
	newMessageRecords, err := putMessages(testCtx, testDB, sessionID, testMessages)
	assert.NoError(t, err, "putMessages should not return an error")

	metadataToMerge := []models.MessageMetadata{
		{
			UUID: newMessageRecords[0].UUID,
			Metadata: map[string]interface{}{
				"tags": map[string]interface{}{"Name": "Google", "Label": "ORG"},
			},
			Key: "system.ner",
		},
		{
			UUID: newMessageRecords[1].UUID,
			Metadata: map[string]interface{}{
				"key_to_overwrite": "new_value",
			},
			Key: "system",
		},
		{
			UUID: newMessageRecords[2].UUID,
			Metadata: map[string]interface{}{
				"key_to_delete": nil,
			},
			Key: "system",
		},
		{
			UUID:     newMessageRecords[3].UUID,
			Key:      "new_top_level_key.new_key",
			Metadata: map[string]interface{}{"key": "value"},
		},
		{
			UUID:     newMessageRecords[4].UUID,
			Key:      "system.newkey",
			Metadata: map[string]interface{}{"key": "value"},
		},
		{
			UUID:     newMessageRecords[5].UUID,
			Key:      "",
			Metadata: map[string]interface{}{"new_top_level_key": "value"},
		},
	}

	expectedMetadata := []models.Message{
		{
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"ner": map[string]interface{}{
						"tags": map[string]interface{}{
							"Name":  "Google",
							"Label": "ORG",
						},
					},
				},
			},
		},
		{
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_overwrite": "new_value",
				},
			},
		},
		{
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_delete": nil,
				},
			},
		},
		{
			Metadata: map[string]interface{}{
				"existing_metadata": "this is existing metadata",
				"new_top_level_key": map[string]interface{}{
					"new_key": map[string]interface{}{
						"key": "value",
					},
				},
			},
		},
		{
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"some_other_system_key": "some_other_system_value",
					"newkey": map[string]interface{}{
						"key": "value",
					},
				},
			},
		},
		{
			Metadata: map[string]interface{}{
				"existing_key":      "existing_value",
				"new_top_level_key": "value",
			},
		},
	}

	testCases := []map[string]interface{}{
		{
			"description":           "NestedSystemMetadata",
			"metadataToMergeIndex":  0,
			"expectedMetadataIndex": 0,
		},
		{
			"description":           "OverwriteKey",
			"metadataToMergeIndex":  1,
			"expectedMetadataIndex": 1,
		},
		{
			"description":           "DeleteKey",
			"metadataToMergeIndex":  2,
			"expectedMetadataIndex": 2,
		},
		{
			"description":           "NewTopLevelKeyWithPath",
			"metadataToMergeIndex":  3,
			"expectedMetadataIndex": 3,
		},
		{
			"description":           "AddNewKeyToExistingSystem",
			"metadataToMergeIndex":  4,
			"expectedMetadataIndex": 4,
		},
		{
			"description":           "AddNewTopLevelKeyNoPath",
			"metadataToMergeIndex":  5,
			"expectedMetadataIndex": 5,
		},
	}

	// Call putMetadata function with isPrivileged = true
	err = putMessageMetadata(testCtx, testDB, sessionID, metadataToMerge, true)
	assert.NoError(t, err, "putMetadata should not return an error")

	msgs, err := getMessages(testCtx, testDB, sessionID, 12, &models.Summary{}, 0)
	assert.NoError(t, err, "getMessages should not return an error")

	for _, testCase := range testCases {
		t.Run(testCase["description"].(string), func(t *testing.T) {
			metadataToMergeIndex := testCase["metadataToMergeIndex"].(int)
			expectedMetadataIndex := testCase["expectedMetadataIndex"].(int)

			msg := msgs[metadataToMergeIndex]
			expectedMeta := expectedMetadata[expectedMetadataIndex].Metadata
			assert.Equal(t, expectedMeta, msg.Metadata)
		})
	}

}
