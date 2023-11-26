package postgres

import (
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"

	"github.com/stretchr/testify/assert"
)

func TestMergeMetadata_SessionDeleted(t *testing.T) {
	// Create a test session
	sessionID := testutils.GenerateRandomString(16)
	metadata := map[string]interface{}{
		"key1": "value1",
		"key2": "value2",
	}
	_, err := testDB.NewInsert().
		Model(&SessionSchema{SessionID: sessionID, Metadata: metadata}).
		Exec(testCtx)
	assert.NoError(t, err)

	// Delete the session record
	_, err = testDB.NewDelete().
		Model(&SessionSchema{}).
		Where("session_id = ?", sessionID).
		Exec(testCtx)
	assert.NoError(t, err)

	// Call mergeMetadata with new metadata
	newMetadata := map[string]interface{}{
		"key2": "new-value2",
		"key3": "value3",
	}
	mergedMetadata, err := mergeMetadata(testCtx, testDB,
		"session_id", sessionID, "session", newMetadata, false)

	// Assert that mergeMetadata doesn't return an error even though the session record doesn't exist
	assert.NoError(t, err)

	expectedMetadata := map[string]interface{}{
		"key1": "value1",
		"key2": "new-value2",
		"key3": "value3",
	}

	// Assert that the returned metadata is equal to the new metadata, since the old metadata doesn't exist
	assert.Equal(t, expectedMetadata, mergedMetadata)
}

func Test_mergeMetadata(t *testing.T) {
	// Initialize SessionDAO
	dao := NewSessionDAO(testDB)

	// Create a test session
	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"A": 1,
			"B": map[string]interface{}{
				"C": 2,
			},
			"Z":  3,
			"YY": "this should be removed",
		},
	}
	_, err = dao.Create(testCtx, session)
	assert.NoError(t, err)

	tests := []struct {
		name             string
		sessionID        string
		metadata         map[string]interface{}
		privileged       bool
		expectedError    error
		expectedMetadata map[string]interface{}
	}{
		{
			name:      "Update metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 3, // Should override initial value of "A"
				"B": map[string]interface{}{
					"D": 4, // Should be added to map under "B"
					"E": map[string]interface{}{
						"F": 5, // Test deeply nested map
					},
				},
				"YY": nil,
			},
			privileged: false,
			expectedMetadata: map[string]interface{}{
				"A": 3, // Updated value
				"B": map[string]interface{}{
					"C": 2, // Initial value
					"D": 4, // New value
					"E": map[string]interface{}{
						"F": 5, // New value from deeply nested map
					},
				},
				"Z":  3, // Initial value
				"YY": nil,
			},
		},
		{
			name:      "Unprivileged update with system metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"system": map[string]interface{}{
					"foo": "bar", // This should be ignored
				},
				"YY": nil,
			},
			privileged: false,
			expectedMetadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"Z":  3, // Initial value
				"YY": nil,
			},
		},
		{
			name:      "Privileged update with system metadata",
			sessionID: sessionID,
			metadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"system": map[string]interface{}{
					"foo": "bar", // This should NOT be ignored
				},
				"YY": nil,
			},
			privileged: true,
			expectedMetadata: map[string]interface{}{
				"A": 1,
				"B": map[string]interface{}{
					"C": 2,
				},
				"Z": 3, // Initial value
				"system": map[string]interface{}{
					"foo": "bar",
				},
				"YY": nil,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mergedMetadata, err := mergeMetadata(
				testCtx,
				testDB,
				"session_id",
				tt.sessionID,
				"session",
				tt.metadata,
				tt.privileged,
			)

			if tt.expectedError != nil {
				assert.Error(t, err)
				assert.Equal(t, tt.expectedError, err)
			} else {
				assert.NoError(t, err)

				// Compare the expected metadata and merged metadata
				assertEqualMaps(t, tt.expectedMetadata, mergedMetadata)
			}
		})
	}
}

// assertEqualMaps asserts that two maps are equal, ignoring the type of float / int values.
func assertEqualMaps(t *testing.T, expected, actual map[string]interface{}) {
	t.Helper()
	assert.Equal(t, len(expected), len(actual))

	for k, v := range expected {
		switch v := v.(type) {
		case int:
			switch actual[k].(type) {
			case float64:
				assert.Equal(t, float64(v), actual[k])
			default:
				assert.Equal(t, v, actual[k])
			}
		case map[string]interface{}:
			assertEqualMaps(t, v, actual[k].(map[string]interface{}))
		default:
			assert.Equal(t, v, actual[k])
		}
	}
}
