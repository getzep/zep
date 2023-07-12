package postgres

import (
	"strings"
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestPutUnPrivilegedMetadata(t *testing.T) {
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	_, err = putSession(testCtx, testDB, sessionID, nil, true)
	assert.NoError(t, err, "putSession should not return an error")

	testMessages := []MessageStoreSchema{
		{
			SessionID: sessionID,
			Role:      "human",
			Content:   "Hello again",
			Metadata: map[string]interface{}{
				"some": "data",
			},
		},
		{
			SessionID: sessionID,
			Role:      "human",
			Content:   "Hello again",
			Metadata: map[string]interface{}{
				"foo": "bar",
				"system": map[string]interface{}{
					"this_should_be": "retained",
				},
			},
		},
	}

	insertMessages(t, testMessages)

	metadataToMerge := []models.Message{
		{
			UUID: testMessages[0].UUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"this_should_not": "be_stored",
				},
			},
		},
		{
			UUID: testMessages[1].UUID,
			Metadata: map[string]interface{}{
				"this_should": "be_stored",
			},
		},
	}

	expectedMetadata := []models.Message{
		{
			Metadata: map[string]interface{}{
				"some": "data",
			},
		},
		{
			Metadata: map[string]interface{}{
				"foo":         "bar",
				"this_should": "be_stored",
				"system": map[string]interface{}{
					"this_should_be": "retained",
				},
			},
		},
	}

	testCases := []map[string]interface{}{
		{
			"description":           "AttemptToStoreSystemKey",
			"metadataToMergeIndex":  0,
			"expectedMetadataIndex": 0,
		},
		{
			"description":           "RetainSystemAddNewKey",
			"metadataToMergeIndex":  1,
			"expectedMetadataIndex": 1,
		},
	}

	// Call putMetadata function with isPrivileged = true
	_, err = putMessageMetadata(testCtx, testDB, sessionID, metadataToMerge, false)
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

func TestPutMetadata(t *testing.T) {
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")
	_, err = putSession(testCtx, testDB, sessionID, nil, true)
	assert.NoError(t, err, "putSession should not return an error")

	testMessages := []MessageStoreSchema{
		{
			SessionID: sessionID,
			Role:      "user",
			Content:   "Hello",
		},
		{
			SessionID: sessionID,
			Role:      "human",
			Content:   "Hello again",
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_overwrite": "value_to_overwrite",
				},
			},
		},
		{
			SessionID: sessionID,
			Role:      "human",
			Content:   "Hello again",
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_delete": "value_to_delete",
				},
			},
		},
		{
			SessionID: sessionID,
			Role:      "bot",
			Content:   "Hi there!",
			Metadata:  map[string]interface{}{"existing_metadata": "this is existing metadata"},
		},
		{
			SessionID: sessionID,
			Role:      "human",
			Content:   "Bonjour!",
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"some_other_system_key": "some_other_system_value",
				},
			},
		},
		{
			SessionID: sessionID,
			Role:      "human",
			Content:   "Hi!",
			Metadata:  map[string]interface{}{"existing_key": "existing_value"},
		},
	}

	insertMessages(t, testMessages)

	metadataToMerge := []models.Message{
		{
			UUID: testMessages[0].UUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"ner": map[string]interface{}{
						"tags": map[string]interface{}{"Name": "Google", "Label": "ORG"},
					},
				},
			},
		},
		{
			UUID: testMessages[1].UUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_overwrite": "new_value",
				},
			},
		},
		{
			UUID: testMessages[2].UUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"key_to_delete": nil,
				},
			},
		},
		{
			UUID: testMessages[3].UUID,
			Metadata: map[string]interface{}{
				"new_top_level_key": map[string]interface{}{
					"new_key": map[string]interface{}{
						"key": "value",
					},
				},
			},
		},
		{
			UUID: testMessages[4].UUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{
					"newkey": map[string]interface{}{
						"key": "value",
					},
				},
			},
		},
		{
			UUID: testMessages[5].UUID,
			Metadata: map[string]interface{}{
				"new_top_level_key": "value",
			},
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
	_, err = putMessageMetadata(testCtx, testDB, sessionID, metadataToMerge, true)
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

func insertMessages(t *testing.T, testMessages []MessageStoreSchema) {
	var cols = []string{
		"id",
		"created_at",
		"uuid",
		"session_id",
		"role",
		"content",
		"token_count",
		"metadata",
	}

	_, err := testDB.NewInsert().
		Model(&testMessages).
		Column(cols...).
		On("CONFLICT (uuid) DO UPDATE").
		Returning(strings.Join(cols, ",")).
		Exec(testCtx)
	assert.NoError(t, err, "messages save should not return an error")
}
