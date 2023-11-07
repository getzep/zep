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

type MultiQuestionRetriever struct {
	appState        *models.AppState
	SessionID       string
	LastN           int
	QuestionCount   int
	HistoryMessages []models.Message
	History         string
	Service         string
}

func NewMultiQuestionRetriever(
	appState *models.AppState,
	sessionID string,
	questionCount int,
	messages []models.Message,
	service string,
) *MultiQuestionRetriever {
	return &MultiQuestionRetriever{
		appState:        appState,
		SessionID:       sessionID,
		LastN:           len(messages),
		QuestionCount:   questionCount,
		HistoryMessages: messages,
		Service:         service,
	}
}

func (m *MultiQuestionRetriever) Retrieve(ctx context.Context) ([]string, error) {
	questions, err := m.generateQuestions(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to generate questions: %w", err)
	}

	return questions, nil
}

func (m *MultiQuestionRetriever) search(ctx context.Context, questions []string) ([]string, error) {
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

	var uniqueSummaries = make(map[string]string)
	for _, res := range results {
		for _, item := range res {
			uniqueSummaries[item.Summary.UUID.String()] = item.Summary.Content
		}
	}

	var summaries = make([]string, len(uniqueSummaries))
	i := 0
	for _, summary := range uniqueSummaries {
		summaries[i] = summary
		i++
	}

	return summaries, nil
}

func (m *MultiQuestionRetriever) generateQuestions(ctx context.Context) ([]string, error) {
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

func (m *MultiQuestionRetriever) generateQuestionsOpenAI(ctx context.Context) ([]string, error) {
	// Create a prompt with the Message input that needs to be classified
	prompt, err := internal.ParsePrompt(defaultMultiRetrieverTemplateOpenAI, m)
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

	return questions, nil
}

func (m *MultiQuestionRetriever) extractQuestions(xmlData string) []string {
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
func (m *MultiQuestionRetriever) generateHistoryString() string {
	var builder strings.Builder

	for _, m := range m.HistoryMessages {
		messageText := fmt.Sprintf("%s: %s", m.Role, m.Content)
		builder.WriteString(messageText + "\n")
	}

	return builder.String()
}

const defaultMultiRetrieverTemplateAnthropic = `
Review the Current Summary inside <current_summary></current_summary> XML tags, 
and the New Lines of the provided conversation inside the <new_lines></new_lines> XML tags. Create a concise summary 
of the conversation, adding from the <new_lines> to the <current_summary>.
If the New Lines are meaningless or empty, return the <current_summary>.

Here is an example:
<example>
<current_summary>
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the 
lead singer.
<current_summary>
<new_lines>
Human: Who were the other members of Led Zeppelin?
Assistant: The other founding members of Led Zeppelin were Jimmy Page (guitar), John Paul Jones (bass, keyboards), and 
John Bonham (drums).
</new_lines> 
Assistant: The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the lead
singer and lists the founding members as Jimmy Page, John Paul Jones, and John Bonham.
</example>

<current_summary>
{{.PrevSummary}}
</current_summary>
<new_lines>
{{.MessagesJoined}}
</new_lines>

Provide a response immediately without preamble.
`

const defaultMultiRetrieverTemplateOpenAI = `
You are an AI language model assistant. The last {{.LastN}} messages between an AI and a human may be found below. 
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
