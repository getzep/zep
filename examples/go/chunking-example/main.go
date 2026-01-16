package main

import (
	"context"
	"fmt"
	"log"
	"math"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/getzep/zep-go/v2"
	zepclient "github.com/getzep/zep-go/v2/client"
	"github.com/getzep/zep-go/v2/option"
	"github.com/joho/godotenv"
	openai "github.com/sashabaranov/go-openai"
)

// Configuration
const (
	ChunkSize          = 500
	ChunkOverlap       = 50
	ZepMaxEpisodeSize  = 10000
	OpenAIModel        = "gpt-5-mini-2025-08-07"
	MaxRetries         = 3
)

// chunkDocument splits a document into chunks with overlap
func chunkDocument(text string) []string {
	paragraphs := regexp.MustCompile(`\n\s*\n`).Split(text, -1)

	var chunks []string
	var currentChunk strings.Builder

	for _, para := range paragraphs {
		para = strings.TrimSpace(para)
		if para == "" {
			continue
		}

		if len(para) > ChunkSize {
			sentences := splitIntoSentences(para)
			for _, sentence := range sentences {
				sentence = strings.TrimSpace(sentence)
				if sentence == "" {
					continue
				}

				if currentChunk.Len()+len(sentence)+1 > ChunkSize && currentChunk.Len() > 0 {
					chunks = append(chunks, currentChunk.String())
					overlapText := getOverlapText(currentChunk.String(), ChunkOverlap)
					currentChunk.Reset()
					currentChunk.WriteString(overlapText)
				}

				if currentChunk.Len() > 0 {
					currentChunk.WriteString(" ")
				}
				currentChunk.WriteString(sentence)
			}
		} else {
			if currentChunk.Len()+len(para)+2 > ChunkSize && currentChunk.Len() > 0 {
				chunks = append(chunks, currentChunk.String())
				overlapText := getOverlapText(currentChunk.String(), ChunkOverlap)
				currentChunk.Reset()
				currentChunk.WriteString(overlapText)
			}

			if currentChunk.Len() > 0 {
				currentChunk.WriteString("\n\n")
			}
			currentChunk.WriteString(para)
		}
	}

	if currentChunk.Len() > 0 {
		chunks = append(chunks, currentChunk.String())
	}

	return chunks
}

// splitIntoSentences splits text into sentences
func splitIntoSentences(text string) []string {
	re := regexp.MustCompile(`([.!?]+)\s+`)
	parts := re.Split(text, -1)
	delimiters := re.FindAllString(text, -1)

	var sentences []string
	for i, part := range parts {
		if part == "" {
			continue
		}
		sentence := part
		if i < len(delimiters) {
			sentence += strings.TrimSpace(delimiters[i])
		}
		sentences = append(sentences, sentence)
	}
	return sentences
}

// getOverlapText extracts overlap text from the end of a string
func getOverlapText(text string, overlapSize int) string {
	if len(text) <= overlapSize {
		return text
	}

	overlap := text[len(text)-overlapSize:]
	spaceIdx := strings.Index(overlap, " ")
	if spaceIdx > 0 && spaceIdx < len(overlap)/2 {
		overlap = overlap[spaceIdx+1:]
	}
	return overlap
}

// contextualizeChunk adds context to a chunk using OpenAI
func contextualizeChunk(ctx context.Context, client *openai.Client, fullDoc, chunk string) (string, error) {
	prompt := fmt.Sprintf(`<document>
%s
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
%s
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. If the document has a publication date, please include the date in your context. Answer only with the succinct context and nothing else.`, fullDoc, chunk)

	var lastErr error

	for attempt := 0; attempt < MaxRetries; attempt++ {
		if attempt > 0 {
			waitTime := time.Duration(math.Pow(2, float64(attempt))) * time.Second
			log.Printf("Rate limited, waiting %v before retry...", waitTime)
			time.Sleep(waitTime)
		}

		resp, err := client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
			Model: OpenAIModel,
			Messages: []openai.ChatCompletionMessage{
				{
					Role:    openai.ChatMessageRoleUser,
					Content: prompt,
				},
			},
			MaxCompletionTokens: 256,
		})

		if err != nil {
			lastErr = err
			if strings.Contains(err.Error(), "rate limit") || strings.Contains(err.Error(), "429") {
				continue
			}
			return "", fmt.Errorf("OpenAI API error: %w", err)
		}

		if len(resp.Choices) == 0 {
			return "", fmt.Errorf("no response from OpenAI")
		}

		contextText := strings.TrimSpace(resp.Choices[0].Message.Content)
		return fmt.Sprintf("%s\n\n---\n\n%s", contextText, chunk), nil
	}

	return "", fmt.Errorf("max retries exceeded: %w", lastErr)
}

// validateAndTruncate ensures the contextualized chunk fits within Zep limits
func validateAndTruncate(contextualizedChunk, originalChunk string) string {
	if len(contextualizedChunk) <= ZepMaxEpisodeSize {
		return contextualizedChunk
	}

	separator := "\n\n---\n\n"
	maxContextLen := ZepMaxEpisodeSize - len(originalChunk) - len(separator)

	if maxContextLen <= 0 {
		log.Printf("Warning: Original chunk exceeds Zep limit, truncating")
		return originalChunk[:ZepMaxEpisodeSize]
	}

	parts := strings.SplitN(contextualizedChunk, separator, 2)
	if len(parts) < 2 {
		return contextualizedChunk[:ZepMaxEpisodeSize]
	}

	truncatedContext := parts[0]
	if len(truncatedContext) > maxContextLen {
		truncatedContext = truncatedContext[:maxContextLen] + "..."
	}

	return fmt.Sprintf("%s%s%s", truncatedContext, separator, originalChunk)
}

// ensureUserExists checks if a user exists in Zep and creates them if not
func ensureUserExists(ctx context.Context, client *zepclient.Client, userID string) error {
	_, err := client.User.Get(ctx, userID)
	if err == nil {
		log.Printf("User %s already exists", userID)
		return nil
	}

	log.Printf("Creating user %s", userID)
	_, err = client.User.Add(ctx, &zep.CreateUserRequest{
		UserID: zep.String(userID),
	})
	if err != nil {
		return fmt.Errorf("failed to create user: %w", err)
	}

	log.Printf("User %s created successfully", userID)
	return nil
}

// ingestToZep sends a chunk to Zep with retry logic
func ingestToZep(ctx context.Context, client *zepclient.Client, userID, data string) error {
	var lastErr error

	for attempt := 0; attempt < MaxRetries; attempt++ {
		if attempt > 0 {
			waitTime := time.Duration(math.Pow(2, float64(attempt))) * time.Second
			log.Printf("Retrying Zep ingestion after %v", waitTime)
			time.Sleep(waitTime)
		}

		dataType := zep.GraphDataTypeText
		_, err := client.Graph.Add(ctx, &zep.AddDataRequest{
			UserID: zep.String(userID),
			Type:   &dataType,
			Data:   zep.String(data),
		})

		if err == nil {
			return nil
		}

		lastErr = err
		log.Printf("Zep ingestion attempt %d failed: %v", attempt+1, err)
	}

	return fmt.Errorf("max retries exceeded for Zep ingestion: %w", lastErr)
}

// processDocument handles the full document processing pipeline
func processDocument(documentPath, userID string) error {
	// Check environment variables
	openaiKey := os.Getenv("OPENAI_API_KEY")
	if openaiKey == "" {
		return fmt.Errorf("OPENAI_API_KEY environment variable is required")
	}

	zepAPIKey := os.Getenv("ZEP_API_KEY")
	if zepAPIKey == "" {
		return fmt.Errorf("ZEP_API_KEY environment variable is required")
	}

	// Read document
	docContent, err := os.ReadFile(documentPath)
	if err != nil {
		return fmt.Errorf("error reading document: %w", err)
	}
	fullDoc := string(docContent)

	log.Printf("Document loaded: %d characters", len(fullDoc))
	log.Printf("Configuration: chunk_size=%d, overlap=%d", ChunkSize, ChunkOverlap)

	// Initialize clients
	ctx := context.Background()
	openaiClient := openai.NewClient(openaiKey)
	zepClient := zepclient.NewClient(option.WithAPIKey(zepAPIKey))

	// Ensure user exists
	if err := ensureUserExists(ctx, zepClient, userID); err != nil {
		return fmt.Errorf("error ensuring user exists: %w", err)
	}

	// Chunk the document
	chunks := chunkDocument(fullDoc)
	log.Printf("Document split into %d chunks", len(chunks))

	// Process chunks
	for i, chunk := range chunks {
		log.Printf("\n--- Processing chunk %d/%d (%d chars) ---", i+1, len(chunks), len(chunk))

		// Contextualize with OpenAI
		contextualizedChunk, err := contextualizeChunk(ctx, openaiClient, fullDoc, chunk)
		if err != nil {
			log.Printf("Error contextualizing chunk %d: %v", i+1, err)
			continue
		}

		log.Printf("Contextualized chunk: %d -> %d chars", len(chunk), len(contextualizedChunk))

		// Validate and truncate if needed
		finalChunk := validateAndTruncate(contextualizedChunk, chunk)
		if len(finalChunk) != len(contextualizedChunk) {
			log.Printf("Chunk truncated to fit Zep limit: %d chars", len(finalChunk))
		}

		// Ingest to Zep
		if err := ingestToZep(ctx, zepClient, userID, finalChunk); err != nil {
			log.Printf("Error ingesting chunk %d: %v", i+1, err)
			continue
		}
		log.Printf("Successfully ingested chunk %d", i+1)
	}

	log.Println("\n==================================================")
	log.Println("Processing complete!")
	log.Println("==================================================")

	return nil
}

func main() {
	// Load environment variables
	if err := godotenv.Load(); err != nil {
		log.Println("No .env file found, using environment variables")
	}

	// Example usage - modify these values as needed
	documentPath := "sample_document.txt"
	userID := "example-user"

	if err := processDocument(documentPath, userID); err != nil {
		log.Fatalf("Error: %v", err)
	}
}
