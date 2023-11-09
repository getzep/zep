package search

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/getzep/zep/internal"
	"github.com/sourcegraph/conc/pool"

	"github.com/getzep/zep/pkg/models"
)

const PerpetualMemoryMinScore = 0.7
const PerpetualMemoryMMRLambda = 0.5
const PerpetualMemorySummaryCount = 3
const PerpetualMemoryLLMTimeOut = 20 * time.Second

var questionExtractRe = regexp.MustCompile(`(?s)<questions>\s*(.*?)\s*</questions>`)

type MultiQuestionSummaryRetriever struct {
	appState        *models.AppState
	SessionID       string
	LastN           int
	QuestionCount   int
	HistoryMessages []models.Message
	History         string
	Service         string
}

func NewMultiQuestionSummaryRetriever(
	appState *models.AppState,
	sessionID string,
	questionCount int,
	messages []models.Message,
	service string,
) *MultiQuestionSummaryRetriever {
	return &MultiQuestionSummaryRetriever{
		appState:        appState,
		SessionID:       sessionID,
		LastN:           len(messages),
		QuestionCount:   questionCount,
		HistoryMessages: messages,
		Service:         service,
	}
}

func (m *MultiQuestionSummaryRetriever) Run(ctx context.Context) ([]models.Summary, error) {
	questions, err := m.generateQuestions(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to generate questions: %w", err)
	}

	return m.search(ctx, questions)
}

func (m *MultiQuestionSummaryRetriever) search(ctx context.Context, questions []string) ([]models.Summary, error) {
	ctx, cancel := context.WithTimeout(ctx, PerpetualMemoryLLMTimeOut)
	defer cancel()

	pool := pool.NewWithResults[[]models.MemorySearchResult]().WithContext(ctx).WithCancelOnError().WithFirstError()

	searchQuestion := func(question string) ([]models.MemorySearchResult, error) {
		p := &models.MemorySearchPayload{
			Text:        question,
			SearchScope: models.SearchScopeSummary,
			SearchType:  models.SearchTypeMMR,
			MinScore:    PerpetualMemoryMinScore,
			MMRLambda:   PerpetualMemoryMMRLambda,
		}

		r, err := m.appState.MemoryStore.SearchMemory(
			ctx,
			m.appState,
			m.SessionID,
			p,
			PerpetualMemorySummaryCount,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to search summaries: %w", err)
		}

		return r, nil
	}

	for _, question := range questions {
		question := question
		pool.Go(func(ctx context.Context) ([]models.MemorySearchResult, error) {
			return searchQuestion(question)
		})
	}
	results, err := pool.Wait()
	if err != nil {
		return nil, fmt.Errorf("failed to search summaries: %w", err)
	}

	var uniqueSummaries = make(map[string]*models.Summary)
	for _, res := range results {
		for _, item := range res {
			uniqueSummaries[item.Summary.UUID.String()] = item.Summary
		}
	}

	var summaries = make([]models.Summary, len(uniqueSummaries))
	i := 0
	for _, summary := range uniqueSummaries {
		summaries[i] = *summary
		i++
	}

	log.Debugf("Found %d unique summaries", len(summaries))
	log.Debugf("Summaries: %+v", summaries)

	if len(summaries) > 1 {
		summary, err := m.reduce(ctx, summaries)
		if err != nil {
			return nil, fmt.Errorf("failed to reduce summaries: %w", err)
		}

		summaries = []models.Summary{summary}
	}

	return summaries, nil
}

func (m *MultiQuestionSummaryRetriever) generateQuestions(ctx context.Context) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, PerpetualMemoryLLMTimeOut)
	defer cancel()

	if len(m.HistoryMessages) == 0 {
		return nil, errors.New("no messages provided")
	}
	m.History = m.generateHistoryString()

	switch m.Service {
	case "openai":
		return m.generateQuestionsOpenAI(ctx)
		//case "anthropic":
		//	return m.generateQuestionsAnthropic()
	}

	return nil, errors.New("unsupported service")
}

func (m *MultiQuestionSummaryRetriever) reduce(ctx context.Context, summaries []models.Summary) (models.Summary, error) {
	ctx, cancel := context.WithTimeout(ctx, PerpetualMemoryLLMTimeOut)
	defer cancel()

	if len(summaries) == 0 {
		return models.Summary{}, errors.New("no summaries provided")
	}

	switch m.Service {
	case "openai":
		return m.reduceOpenAI(ctx, summaries)
		//case "anthropic":
		//	return m.reduceAnthropic(summaries)
	}

	return models.Summary{}, errors.New("unsupported service")
}

func (m *MultiQuestionSummaryRetriever) reduceOpenAI(ctx context.Context, summaries []models.Summary) (models.Summary, error) {
	prompt, err := internal.ParsePrompt(defaultMultiRetrieverReduceTemplateOpenAI, summaries)
	if err != nil {
		return models.Summary{}, fmt.Errorf("reduceOpenAI failed: %w", err)
	}

	// Send the populated prompt to the language model
	summaryText, err := m.appState.LLMClient.Call(
		ctx,
		prompt,
	)
	if err != nil {
		return models.Summary{}, fmt.Errorf("reduceOpenAI failed: %w", err)
	}

	summary := models.Summary{
		Content: summaryText,
	}

	return summary, nil
}

func (m *MultiQuestionSummaryRetriever) generateQuestionsOpenAI(ctx context.Context) ([]string, error) {
	// Create a prompt with the Message input that needs to be classified
	prompt, err := internal.ParsePrompt(defaultMultiRetrieverQuestionsTemplateOpenAI, m)
	if err != nil {
		return nil, fmt.Errorf("generateQuestionsOpenAI failed: %w", err)
	}

	// Send the populated prompt to the language model
	questionText, err := m.appState.LLMClient.Call(
		ctx,
		prompt,
	)
	if err != nil {
		return nil, fmt.Errorf("generateQuestionsOpenAI failed: %w", err)
	}

	questions := m.extractQuestions(questionText)
	if len(questions) == 0 {
		return nil, errors.New("no questions generated")
	}

	log.Debugf("Generated %d questions", len(questions))
	log.Debugf("Questions: %+v", questions)

	return questions, nil
}

func (m *MultiQuestionSummaryRetriever) extractQuestions(xmlData string) []string {
	matches := questionExtractRe.FindStringSubmatch(xmlData)
	if len(matches) < 2 {
		return nil
	}

	// Split the matched string into a slice of strings, trimming whitespace and removing empty strings
	questions := strings.Split(matches[1], "\n")
	nonEmptyQuestions := make([]string, 0, len(questions))
	for _, question := range questions {
		trimmed := strings.TrimSpace(question)
		if trimmed != "" {
			nonEmptyQuestions = append(nonEmptyQuestions, trimmed)
		}
	}

	return nonEmptyQuestions
}

// generateHistoryString generates a chat history string from the Message slice pasted to it.
func (m *MultiQuestionSummaryRetriever) generateHistoryString() string {
	var builder strings.Builder

	for _, m := range m.HistoryMessages {
		messageText := fmt.Sprintf("%s: %s", m.Role, m.Content)
		builder.WriteString(messageText + "\n")
	}

	return builder.String()
}

const defaultMultiRetrieverQuestionsTemplateOpenAI = `
The last {{.LastN}} messages between an AI and a human may be found below. 
Your task is to generate {{.QuestionCount}} different questions directly related to the chat message history. 
We will use these questions to retrieve relevant past conversations from a vector database.
By generating multiple perspectives on the chat history, your goal is to help the user overcome some of the 
limitations of distance-based similarity search.

Provide these alternative questions separated by newlines between XML tags. For example:

<questions>
Question 1
Question 2
Question 3
</questions>

Historical chat messages:
{{.History}}
`

const defaultMultiRetrieverReduceTemplateOpenAI = `
Below are several summaries generated from the chat history between a human and an AI.
Create a single, consolidated summary from these summaries. Ensure that it is concise, but don't remove any
important information.

START SUMMARIES
{{range .}}
{{.Content}}
{{end}}
END SUMMARIES

New Summary:
`
