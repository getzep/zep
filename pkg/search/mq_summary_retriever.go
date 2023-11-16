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
const PerpetualMemoryTimeOut = 10 * time.Second

var questionExtractRe = regexp.MustCompile(`(?s)<questions>\s*(.*?)\s*</questions>`)

type MultiQuestionSummaryRetriever struct {
	appState        *models.AppState
	SessionID       string
	LastN           int
	HistoryMessages []models.Message
	QuestionCount   int
	Questions       []string
	SearchResults   []models.Summary
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

func (m *MultiQuestionSummaryRetriever) Run(ctx context.Context) (*models.Summary, error) {
	ctx, cancel := context.WithTimeout(ctx, PerpetualMemoryTimeOut)
	defer cancel()

	questions, err := m.generateQuestions(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to generate questions: %w", err)
	}

	m.Questions = questions

	return m.search(ctx, questions)
}

func (m *MultiQuestionSummaryRetriever) search(
	ctx context.Context,
	questions []string,
) (*models.Summary, error) {
	searchPool := pool.NewWithResults[[]models.MemorySearchResult]().WithContext(ctx).
		WithCancelOnError().
		WithFirstError()

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
		searchPool.Go(func(ctx context.Context) ([]models.MemorySearchResult, error) {
			return searchQuestion(question)
		})
	}
	results, err := searchPool.Wait()
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

	m.SearchResults = summaries

	log.Debugf("Found %d unique summaries", len(summaries))
	log.Debugf("Summaries: %+v", summaries)

	var summary *models.Summary
	switch len(summaries) {
	case 0:
		return nil, nil
	case 1:
		summary = &summaries[0]
	default:
		summary, err = m.reduce(ctx)
		if err != nil {
			return nil, fmt.Errorf("failed to reduce summaries: %w", err)
		}
	}

	return summary, nil
}

func (m *MultiQuestionSummaryRetriever) generateQuestions(ctx context.Context) ([]string, error) {
	if len(m.HistoryMessages) == 0 {
		return nil, errors.New("no messages provided")
	}

	var prompt string
	var err error
	switch m.Service {
	case "openai":
		prompt, err = internal.ParsePrompt(defaultMultiRetrieverQuestionsTemplateOpenAI, m)
	case "anthropic":
		prompt, err = internal.ParsePrompt(defaultMultiRetrieverQuestionsTemplateAnthropic, m)
	default:
		return nil, fmt.Errorf("unknown service: %s", m.Service)
	}
	if err != nil {
		return nil, fmt.Errorf("generateQuestions failed: %w", err)
	}

	log.Debugf("generateQuestions prompt: %s", prompt)

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

func (m *MultiQuestionSummaryRetriever) reduce(
	ctx context.Context,
) (*models.Summary, error) {
	if len(m.SearchResults) == 0 {
		return nil, errors.New("no summaries provided")
	}

	var prompt string
	var err error
	switch m.Service {
	case "openai":
		prompt, err = internal.ParsePrompt(defaultMultiRetrieverReduceTemplateOpenAI, m)
	case "anthropic":
		prompt, err = internal.ParsePrompt(defaultMultiRetrieverReduceTemplateAnthropic, m)
	default:
		return nil, fmt.Errorf("unknown service: %s", m.Service)
	}
	if err != nil {
		return nil, fmt.Errorf("reduce failed: %w", err)
	}

	log.Debugf("reduce prompt: %s", prompt)

	summaryText, err := m.appState.LLMClient.Call(
		ctx,
		prompt,
	)
	if err != nil {
		return nil, fmt.Errorf("reduceOpenAI failed: %w", err)
	}

	summary := models.Summary{
		Content: summaryText,
	}

	return &summary, nil
}

const defaultMultiRetrieverQuestionsTemplateOpenAI = `The last {{.LastN}} messages between an AI and a human may be found below. 
Your task is to generate {{if eq .QuestionCount 1}}a question{{else}}{{.QuestionCount}} different questions{{end}} 
directly related to the most recent message. Use the Historical chat messages as additional context.

We will use these questions to retrieve relevant past conversations from a vector database. By generating multiple 
perspectives on the chat history, your goal is to help the user overcome some of the limitations of 
distance-based similarity search.

Provide {{if eq .QuestionCount 1}}this question{{else}}these alternative questions separated by newlines{{end}} 
between XML tags. For example:

<questions>
Question 1
{{if ne .QuestionCount 1}}
Question 2
Question 3
{{end}}
</questions>

Historical chat messages:
{{ $chatHistory := mustInitial .HistoryMessages }}
{{ $lastMessage := mustLast .HistoryMessages }}
{{range $chatHistory}}
{{.Role}}: {{.Content}}
{{end}}

Most recent message: 
{{ $lastMessage.Role }}: {{ $lastMessage.Content }}

Questions:
`

const defaultMultiRetrieverReduceTemplateOpenAI = `Below are several summaries generated from the chat history between a human and an AI.
Create a single, concise, consolidated summary from these summaries. Only include information directly relevant to the questions below. Do not include any
information not in the summaries below.

<questions>
{{range .Questions}}
{{.}}
{{end}}
</questions>

<summaries>
{{range .SearchResults}}
{{.Content}}
{{end}}
</summaries>

New Summary:
`

const defaultMultiRetrieverQuestionsTemplateAnthropic = `The last {{.LastN}} messages between an AI and a human may be found 
between the <chat_history> tags below. 
Your task is to generate {{if eq .QuestionCount 1}}a question{{else}}{{.QuestionCount}} different questions{{end}} 
directly related to the very last message between the <last_message> tags. Use the <chat_history> as additional context.

We will use these questions to retrieve relevant past conversations from a vector database. By generating multiple 
perspectives on the chat history, your goal is to help the user overcome some of the limitations of 
distance-based similarity search.

Provide {{if eq .QuestionCount 1}}this question{{else}}these alternative questions separated by newlines{{end}} 
between <questions></questions> tags. For example:

<questions>
Question 1
{{if ne .QuestionCount 1}}
Question 2
Question 3
{{end}}
</questions>

<chat_history>
{{ $chatHistory := mustInitial .HistoryMessages }}
{{ $lastMessage := mustLast .HistoryMessages }}
{{range $chatHistory}}
{{.Role}}: {{.Content}}
{{end}}
</chat_history>

<last_message>
{{ $lastMessage.Role }}: {{ $lastMessage.Content }}
</last_message>
`

const defaultMultiRetrieverReduceTemplateAnthropic = `Between the <summaries> tags below are several summaries 
generated from the chat history between a human and an AI.
Create a single, concise, consolidated summary from these summaries. Create a single, concise, consolidated summary from these summaries. Do not enclose the answer in tags.

IMPORTANT: Only include information relevant to the questions between the <questions> tags below. Do not include any
information not in the <summaries>.

<questions>
{{range .Questions}}
{{.}}
{{end}}
</questions>

<summaries>
{{range .SearchResults}}
{{.Content}}
{{end}}
</summaries>
`
