package extractors

import (
	"context"
	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/sashabaranov/go-openai"
	"strings"
)

func incrementalSummarizer(
	ctx context.Context,
	openAIClient *openai.Client,
	currentSummary string,
	messages []string,
	summaryMaxTokens int,
) (string, int, error) {
	if len(messages) < 1 {
		return "", 0, NewExtractorError("No messages provided", nil)
	}

	internal.ReverseSlice(messages)

	messagesJoined := strings.Join(messages, "\n")
	prevSummary := ""
	if currentSummary != "" {
		prevSummary = currentSummary
	}

	promptData := SummaryPromptTemplateData{
		PrevSummary:    prevSummary,
		MessagesJoined: messagesJoined,
	}

	progressivePrompt, err := internal.ParsePrompt(summaryPromptTemplate, promptData)
	if err != nil {
		return "", 0, err
	}

	resp, err := llms.RunChatCompletion(ctx, openAIClient, summaryMaxTokens, progressivePrompt)
	if err != nil {
		return "", 0, err
	}

	completion := resp.Choices[0].Message.Content
	tokensUsed := resp.Usage.TotalTokens

	return completion, tokensUsed, nil
}
