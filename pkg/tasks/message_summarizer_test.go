package tasks

import (
	"testing"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/google/uuid"
	"github.com/jinzhu/copier"
	"github.com/stretchr/testify/assert"
)

func runTestSummarize(t *testing.T, llmClient models.ZepLLM) {
	appState.LLMClient = llmClient

	windowSize := 10
	appState.Config.Memory.MessageWindow = windowSize

	newMessageCountAfterSummary := windowSize / 2

	messages := make([]models.Message, len(testutils.TestMessages))
	err := copier.Copy(&messages, &testutils.TestMessages)
	assert.NoError(t, err)

	messages = messages[:windowSize+2]
	for i := range messages {
		messages[i].UUID = uuid.New()
	}

	newestMessageToSummarizeIndex := len(
		messages,
	) - newMessageCountAfterSummary - 1 // the seventh-oldest message, leaving 5 messages after it
	newSummaryPointUUID := messages[newestMessageToSummarizeIndex].UUID

	tests := []struct {
		name     string
		messages []models.Message
		summary  *models.Summary
	}{
		{
			name:     "With an existing summary",
			messages: messages,
			summary: &models.Summary{
				Content:    "Existing summary content",
				TokenCount: 10,
			},
		},
		{
			name:     "With a nil-value passed as the summary argument",
			messages: messages,
			summary:  nil,
		},
	}

	task := NewMessageSummaryTask(appState)
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			newSummary, err := task.summarize(testCtx, tt.messages, tt.summary, 0)
			assert.NoError(t, err)

			assert.Equal(t, newSummaryPointUUID, newSummary.SummaryPointUUID)
			assert.NotEmpty(t, newSummary.Content)
		})
	}
}

func TestSummarize_OpenAI(t *testing.T) {
	appState.Config.LLM.Service = "openai"
	appState.Config.LLM.Model = "gpt-3.5-turbo"
	llmClient, err := llms.NewOpenAILLM(testCtx, appState.Config)
	assert.NoError(t, err)
	runTestSummarize(t, llmClient)
}

func TestSummarize_Anthropic(t *testing.T) {
	appState.Config.LLM.Service = "anthropic"
	appState.Config.LLM.Model = "claude-2"
	llmClient, err := llms.NewAnthropicLLM(testCtx, appState.Config)
	assert.NoError(t, err)
	runTestSummarize(t, llmClient)

	// Reset the config to the default
	appState.Config = testutils.NewTestConfig()
}

func TestValidateSummarizerPrompt(t *testing.T) {
	task := NewMessageSummaryTask(appState)

	testCases := []struct {
		name    string
		prompt  string
		wantErr bool
	}{
		{
			name:    "valid prompt",
			prompt:  "{{.PrevSummary}} {{.MessagesJoined}}",
			wantErr: false,
		},
		{
			name:    "invalid prompt",
			prompt:  "{{.PrevSummary}}",
			wantErr: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := task.validateSummarizerPrompt(tc.prompt)
			if tc.wantErr {
				assert.Error(t, err)
			} else {
				assert.NoError(t, err)
			}
		})
	}
}

func TestGenerateProgressiveSummarizerPrompt(t *testing.T) {
	testCases := []struct {
		name                  string
		service               string
		customPromptOpenAI    string
		customPromptAnthropic string
		expectedPrompt        string
		defaultPrompt         bool
	}{
		{
			name:                  "OpenAI with custom prompt",
			service:               "openai",
			customPromptOpenAI:    "custom openai prompt {{.PrevSummary}} {{.MessagesJoined}}",
			customPromptAnthropic: "",
			expectedPrompt:        "custom openai prompt previous summary joined messages",
		},
		{
			name:                  "Anthropic with custom prompt",
			service:               "anthropic",
			customPromptOpenAI:    "",
			customPromptAnthropic: "custom anthropic prompt {{.PrevSummary}} {{.MessagesJoined}}",
			expectedPrompt:        "custom anthropic prompt previous summary joined messages",
		},
		{
			name:                  "OpenAI without custom prompt",
			service:               "openai",
			customPromptOpenAI:    "",
			customPromptAnthropic: "",
			expectedPrompt:        defaultSummaryPromptTemplateOpenAI,
			defaultPrompt:         true,
		},
		{
			name:                  "Anthropic without custom prompt",
			service:               "anthropic",
			customPromptOpenAI:    "",
			customPromptAnthropic: "",
			expectedPrompt:        defaultSummaryPromptTemplateAnthropic,
			defaultPrompt:         true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			appState := &models.AppState{
				Config: &config.Config{
					LLM: config.LLM{
						Service: tc.service,
					},
					CustomPrompts: config.CustomPromptsConfig{
						SummarizerPrompts: config.ExtractorPromptsConfig{
							OpenAI:    tc.customPromptOpenAI,
							Anthropic: tc.customPromptAnthropic,
						},
					},
				},
			}
			promptData := SummaryPromptTemplateData{
				PrevSummary:    "previous summary",
				MessagesJoined: "joined messages",
			}

			task := NewMessageSummaryTask(appState)

			prompt, err := task.generateProgressiveSummarizerPrompt(promptData)
			assert.NoError(t, err)
			if !tc.defaultPrompt {
				assert.Equal(t, tc.expectedPrompt, prompt)
			} else {
				// Only compare the first 50 characters of the prompt, since the instructions should match
				assert.Equal(t, tc.expectedPrompt[:50], prompt[:50])
			}
		})
	}
}
